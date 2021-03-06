###############################################################################
# Copyright (c) 2011-2016, Pacific Biosciences of California, Inc.
#
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
# * Redistributions of source code must retain the above copyright
#   notice, this list of conditions and the following disclaimer.
# * Redistributions in binary form must reproduce the above copyright
#   notice, this list of conditions and the following disclaimer in the
#   documentation and/or other materials provided with the distribution.
# * Neither the name of Pacific Biosciences nor the names of its
#   contributors may be used to endorse or promote products derived from
#   this software without specific prior written permission.
#
# NO EXPRESS OR IMPLIED LICENSES TO ANY PARTY'S PATENT RIGHTS ARE GRANTED BY
# THIS LICENSE.  THIS SOFTWARE IS PROVIDED BY PACIFIC BIOSCIENCES AND ITS
# CONTRIBUTORS "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT
# NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A
# PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL PACIFIC BIOSCIENCES OR
# ITS CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL,
# EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO,
# PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS;
# OR
# BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER
# IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.
###############################################################################

# Author: Martin D. Smith


""" Input and output functions for DataSet XML files"""

import os.path
import functools
import xml.etree.ElementTree as ET
import logging
from urlparse import urlparse
from pbcore.io.dataset.DataSetMembers import (ExternalResource,
                                              ExternalResources,
                                              DataSetMetadata,
                                              Filters, AutomationParameters,
                                              StatsMetadata)
from pbcore.io.dataset.DataSetWriter import _eleFromDictList
from pbcore.io.dataset.DataSetErrors import InvalidDataSetIOError

log = logging.getLogger(__name__)

def resolveLocation(fname, possibleRelStart=None):
    """Find the absolute path of a file that exists relative to
    possibleRelStart."""
    if possibleRelStart != None:
        if os.path.exists(possibleRelStart):
            if os.path.exists(os.path.join(possibleRelStart, fname)):
                return os.path.abspath(os.path.join(possibleRelStart, fname))
    if os.path.exists(fname):
        return os.path.abspath(fname)
    log.error("Including unresolved file: {f}".format(f=fname))
    return fname

def populateDataSet(dset, filenames):
    for filename in filenames:
        _addFile(dset, filename)
    if filenames:
        dset._populateMetaTypes()

def xmlRootType(fname):
    with open(fname, 'rb') as xml_file:
        tree = ET.parse(xml_file)
    root = tree.getroot()
    return _splitTag(root.tag)[-1]

def _addFile(dset, filename):
    handledTypes = {'xml': _addXmlFile,
                    'bam': _addGenericFile,
                    'fofn': _addFofnFile,
                    '': _addUnknownFile,
                    'file': _addUnknownFile}
    url = urlparse(filename)
    fileType = url.scheme
    fileLocation = url.path.strip()
    if url.netloc:
        fileLocation = url.netloc
    elif os.path.exists(fileLocation):
        fileLocation = os.path.abspath(fileLocation)
    handledTypes[fileType](dset, fileLocation)

def checksts(dset, subdatasets=False):
    if not dset.metadata.summaryStats:
        for extres in dset.externalResources:
            if extres.sts:
                try:
                    dset.loadStats(extres.sts)
                except IOError as e:
                    log.info("Sts.xml file {f} "
                              "unopenable".format(f=extres.sts))
    if subdatasets:
        for sds in dset.subdatasets:
            checksts(sds)

def _addXmlFile(dset, path):
    with open(path, 'rb') as xml_file:
        tree = ET.parse(xml_file)
    root = tree.getroot()
    tmp = _parseXml(type(dset), root)
    tmp.makePathsAbsolute(curStart=os.path.dirname(path))
    checksts(tmp)
    dset.merge(tmp, copyOnMerge=False)

def openFofnFile(path):
    with open(path, 'r') as fofnFile:
        files = []
        for infn in fofnFile:
            infn = infn.strip()
            tmp = os.path.abspath(infn)
            if os.path.exists(tmp):
                files.append(tmp)
            else:
                files.append(os.path.join(os.path.dirname(path), infn))
        return files

def _addFofnFile(dset, path):
    """Open a fofn file by calling parseFiles on the new filename list"""
    files = openFofnFile(path)
    populateDataSet(dset, files)

def _addUnknownFile(dset, path):
    """Open non-uri files
    """
    if path.endswith('xml'):
        return _addXmlFile(dset, path)
    elif path.endswith('bam'):
        return _addGenericFile(dset, path)
    elif path.endswith('fofn'):
        return _addFofnFile(dset, path)
    else:
        # Give it a shot as an XML file:
        try:
            return _addXmlFile(dset, path)
        except ET.ParseError:
            return _addGenericFile(dset, path)

SUB_RESOURCES = ['.scraps.bam', '.control.subreads.bam']
FILE_INDICES = ['.fai', '.pbi', '.bai', '.metadata.xml',
                '.index', '.contig.index', '.sa']

# TODO needs namespace
def _addGenericFile(dset, path):
    """Create and populate an Element object, put it in an available members
    dictionary, return"""
    # filter out resource file types that aren't top level:
    # if we want to exclude scraps as well:
    for ext in SUB_RESOURCES + FILE_INDICES:
        if path.endswith(ext):
            log.debug('Sub resource file {f} given as regular file, '
                      'will be treated '
                      'as a sub resource file instead'.format(f=path))
            return
    extRes = wrapNewResource(path)
    extRess = ExternalResources()
    extRess.append(extRes)
    # We'll update them all at the end, skip updating counts for now
    dset.addExternalResources(extRess, updateCount=False)

# TODO needs namespace
def wrapNewResource(path):
    # filter out non-resource file types:
    for ext in FILE_INDICES:
        if path.endswith(ext):
            log.debug('Index file {f} given as regular file, will be treated '
                      ' as an index file instead'.format(f=path))
            return
    extRes = ExternalResource()
    path = resolveLocation(path)
    extRes.resourceId = path
    index_files = [path + ext for ext in FILE_INDICES if
                   os.path.exists(path + ext)]
    if index_files:
        extRes.addIndices(index_files)

    # Check for sub resources:
    for ext in SUB_RESOURCES:
        filen = '.'.join(path.split('.')[:-2]) + ext
        # don't want to add e.g. scraps to scraps:
        if os.path.exists(filen) and path.endswith('subreads.bam'):
            subres = wrapNewResource(filen)
            setattr(extRes, ext.split('.')[1], subres)
    return extRes


def _parseXml(dsetType, element):
    """Parse an XML DataSet tag, or the root tag of a DataSet XML file (they
    should be equivalent)
    """
    result = dsetType()
    result.objMetadata = element.attrib
    for child in element:
        if child.tag.endswith('ExternalResources'):
            result.externalResources = _parseXmlExtResources(child)
        elif child.tag.endswith('DataSets'):
            result.subdatasets = _parseXmls(dsetType, child)
        elif child.tag.endswith('Filters'):
            result.filters = _parseXmlFilters(child)
        elif child.tag.endswith('DataSetMetadata'):
            result.metadata = _parseXmlDataSetMetadata(child)
        else:
            # Unknown tag found in XML
            pass
    return result

def _parseXmls(dsetType, element):
    """DataSets can exist as elements in other datasets, representing subsets.
    Pull these datasets, parse them, and return a list of them."""
    result = []
    for child in element:
        result.append(_parseXml(dsetType, child))
    return result

def _updateStats(element):
    """This is ugly, hackish and prone to failure. Delete as soon as pre 3.0.16
    sts.xml files can go unsupported"""
    namer = functools.partial(
        _namespaceTag,
        "http://pacificbiosciences.com/PipelineStats/PipeStats.xsd")
    binCounts = [
        './/' + namer('MedianInsertDist') + '/' + namer('BinCount'),
        './/' + namer('ProdDist') + '/' + namer('BinCount'),
        './/' + namer('ReadTypeDist') + '/' + namer('BinCount'),
        './/' + namer('ReadLenDist') + '/' + namer('BinCount'),
        './/' + namer('ReadQualDist') + '/' + namer('BinCount'),
        './/' + namer('InsertReadQualDist') + '/' + namer('BinCount'),
        './/' + namer('InsertReadLenDist') + '/' + namer('BinCount'),
        './/' + namer('ControlReadQualDist') + '/' + namer('BinCount'),
        './/' + namer('ControlReadLenDist') + '/' + namer('BinCount'),
        ]
    binLabels = [
        './/' + namer('ProdDist') + '/' + namer('BinLabel'),
        './/' + namer('ReadTypeDist') + '/' + namer('BinLabel'),
        ]
    for tag in binCounts:
        if element.findall(tag):
            log.info("Outdated stats XML received")
            finds = element.findall(tag)
            parent = tag.split('Dist')[0] + 'Dist'
            parent = element.find(parent)
            for sub_ele in finds:
                parent.remove(sub_ele)
            bce = ET.Element(namer('BinCounts'))
            for sub_ele in finds:
                bce.append(sub_ele)
            parent.append(bce)
    for tag in binLabels:
        if element.findall(tag):
            log.info("Outdated stats XML received")
            finds = element.findall(tag)
            parent = tag.split('Dist')[0] + 'Dist'
            parent = element.find(parent)
            for sub_ele in finds:
                parent.remove(sub_ele)
            bce = ET.Element(namer('BinLabels'))
            for sub_ele in finds:
                bce.append(sub_ele)
            parent.append(bce)
    return element

def _namespaceTag(xmlns, tagName):
    """Preface an XML tag name with the provided namespace"""
    return ''.join(["{", xmlns, "}", tagName])

def _splitTag(tag):
    """Split the namespace and tag name"""
    return [part.strip('{') for part in tag.split('}')]

def _parseXmlExtResources(element):
    """Parse the ExternalResources tag, populating a list of
    ExternalResource objects"""
    return ExternalResources(_eleToDictList(element))

def _parseXmlDataSetMetadata(element):
    """Parse the DataSetMetadata field of XML inputs. This data can be
    extremely extensive."""
    return _eleToDictList(element)

def _eleToDictList(element):
    """A last ditch capture method for uknown Elements"""
    namespace, tag = _splitTag(element.tag)
    text = element.text
    if text:
        text = text.strip()
    attrib = element.attrib
    children = []
    for child in element:
        children.append(_eleToDictList(child))
    return {'tag': tag, 'text': text, 'attrib': attrib,
            'children': children, 'namespace': namespace}

def _parseXmlFilters(element):
    """Pull filters from XML file, put them in a list of dictionaries, where
    each dictionary contains a representation of a Filter tag: key, value pairs
    with parameter names and value expressions.
    """
    return Filters(_eleToDictList(element))

def parseStats(filename):
    url = urlparse(filename)
    fileLocation = url.path.strip()
    if url.netloc:
        fileLocation = url.netloc
    tree = ET.parse(fileLocation)
    root = tree.getroot()
    root = _updateStats(root)
    stats = StatsMetadata(_eleToDictList(root))
    stats.record['tag'] = 'SummaryStats'
    return stats

def parseMetadata(filename):
    url = urlparse(filename)
    fileLocation = url.path.strip()
    if url.netloc:
        fileLocation = url.netloc
    tree = ET.parse(fileLocation)
    dsm_tag = (".//{http://pacificbiosciences.com/PacBioDatasets.xsd}"
               "DataSetMetadata")
    try:
        metadata = _parseXmlDataSetMetadata(tree.getroot().find(dsm_tag))
    except AttributeError:
        # the tag wasn't found, we're trying to do something with None
        raise InvalidDataSetIOError("Unable to parse metadata from "
                                    "{f}".format(f=filename))
    return metadata

