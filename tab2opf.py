#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2007 - Klokan Petr Přidal (www.klokan.cz)
# Copyright (C) 2015 - Alexander Peyser (github.com/apeyser)
# Copyright (C) 2020 - Nicolas Garanis (github.com/nyg)
#
# This library is free software; you can redistribute it and/or
# modify it under the terms of the GNU Library General Public
# License as published by the Free Software Foundation; either
# version 2 of the License, or (at your option) any later version.
#
# This library is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# Library General Public License for more details.
#
# You should have received a copy of the GNU Library General Public
# License along with this library; if not, write to the
# Free Software Foundation, Inc., 59 Temple Place - Suite 330,
# Boston, MA 02111-1307, USA.

import argparse
import importlib
import os
from contextlib import contextmanager
from itertools import islice, count, groupby
import uuid

# Define command-line arguments.
parser = argparse.ArgumentParser("tab2opf")
parser.add_argument("-v", "--verbose", help="print verbose output", action="store_true")
parser.add_argument("-m", "--module", help="import module for mapping, getkey, getdef")
parser.add_argument("-s", "--source", metavar='LANG', default="en", help="source language (e.g. en, fr, de)")
parser.add_argument("-t", "--target", metavar='LANG', default="en", help="target language (e.g. en, fr, de)")
parser.add_argument("-o", "--title", required=True, help="title of the dictionary")
parser.add_argument("file", help="input tab file")
args = parser.parse_args()

VERBOSE = args.verbose
MODULE = args.module
SRC_LANG = args.source
TRG_LANG = args.target
DICT_TITLE = args.title
TAB_FILE = args.file


# Stop with the encoding -- it's broken anyhow
# in the kindles and undefined.
def normalize_letter(ch):
    try:
        ch = mapping[ch]
    except KeyError:
        pass
    return ch


def normalize_unicode(text):
    return ''.join(normalize_letter(c) for c in text)


def loadmember(mod, attr, dfault):
    if hasattr(mod, attr):
        print("Loading {} from {}".format(attr, mod.__name__))
        globals()[attr] = getattr(mod, attr)
    else:
        globals()[attr] = dfault


def importmod():
    global MODULE
    if MODULE is None:
        mod = None
    else:
        mod = importlib.import_module(MODULE)
        print("Loading methods from: {}".format(mod.__file__))

    loadmember(mod, 'getkey', lambda key: key)
    loadmember(mod, 'getdef', lambda dfn: dfn)
    loadmember(mod, 'mapping', {})


importmod()


# add a single [term, definition]
# to defs[key]
# r is a tab split line
def readkey(r, defs):
    try:
        term, defn = r.split('\t', 1)
    except ValueError:
        print("Bad line: '{}'".format(r))
        raise

    term = term.strip()
    defn = getdef(defn)

    nkey = normalize_unicode(term)
    key = getkey(nkey)
    key = key. \
        replace('"', "'"). \
        replace('<', '\\<'). \
        replace('>', '\\>'). \
        lower().strip()

    nkey = nkey. \
        replace('"', "'"). \
        replace('<', '\\<'). \
        replace('>', '\\>'). \
        lower().strip()

    if key == '':
        raise Exception("Missing key {}".format(term))
    if defn == '':
        raise Exception("Missing definition {}".format(term))

    if VERBOSE:
        print(key, ":", term)

    ndef = [term, defn, key == nkey]
    if key in defs:
        defs[key].append(ndef)
    else:
        defs[key] = [ndef]


# Skip empty lines and lines that only have a comment
def inclline(s):
    s = s.lstrip()
    return len(s) != 0 and s[0] != '#'


# Iterate over FILENAME, reading lines of
# term {tab} definition
# skips empty lines and commented out lines
def readkeys():
    if VERBOSE:
        print("Reading {}".format(TAB_FILE))
    with open(TAB_FILE, 'r', encoding='utf-8') as fr:
        defns = {}
        for r in filter(inclline, fr):
            readkey(r, defns)
        return defns


# Write to key file {name}{n}.html
# put the body inside the context manager
# The onclick here gives a kindlegen warning
# but appears to be necessary to actually
# have a lookup dictionary
@contextmanager
def writekeyfile(i):
    fname = 'dictionary-{}-{}-{}.html'.format(SRC_LANG, TRG_LANG, i).lower()
    if VERBOSE:
        print("Key file: {}".format(fname))
    with open(fname, 'w', encoding="utf-8") as to:
        to.write("""<html xmlns:mbp="https://kindlegen.s3.amazonaws.com/AmazonKindlePublishingGuidelines.pdf"
      xmlns:idx="https://kindlegen.s3.amazonaws.com/AmazonKindlePublishingGuidelines.pdf">

<head>
    <meta http-equiv="Content-Type" content="text/html; charset=utf-8"/>
</head>

<body>
    <mbp:pagebreak/>
    <mbp:frameset>
        <mbp:slave-frame display="bottom" device="all" breadth="auto" leftmargin="0" rightmargin="0" bottommargin="0" topmargin="0">
            <div align="center" bgcolor="yellow">
                <a onclick="index_search()">Dictionary Search</a>
            </div>
        </mbp:slave-frame>
        <mbp:pagebreak/>
""")
        try:
            yield to
        finally:
            to.write("""
    </mbp:frameset>
</body>
</html>
""")


# Order definitions by keys, then by whether the key
# matches the original term, then by length of term
# then alphabetically
def keyf(defn):
    term = defn[0]
    if defn[2]:
        l = 0
    else:
        l = len(term)
    return l, term


# Write into to the key, definition pairs
# key -> [[term, defn, key==term]]
def writekey(to, key, defn):
    terms = iter(sorted(defn, key=keyf))
    for term, g in groupby(terms, key=lambda d: d[0]):
        to.write(
            """
        <idx:entry name="word" scriptable="yes">
            <idx:orth value="{key}"><strong>{term}</strong></idx:orth>
            """.format(term=term, key=key))

        to.write('; '.join(ndefn for _, ndefn, _ in g))
        to.write(
            """        </idx:entry>
"""
        )

    if VERBOSE:
        print(key)


# Write all the keys, where defns is a map of
# key --> [[term, defn, key==term]...]
# and name is the basename
# The files are split so that there are no more than
# 10,000 keys written to each file (why?? I dunno)
#
# Returns the number of files.
def writekeys(defns):
    keyit = iter(sorted(defns))
    for j in count():
        keys = list(islice(keyit, 10000))
        if len(keys) == 0:
            break
        else:
            with writekeyfile(j) as to:
                for key in keys:
                    writekey(to, key, defns[key])
    return j


# After writing keys, the opf that references all the key files
# is constructed.
# openopf wraps the contents of writeopf
@contextmanager
def openopf():
    fname = 'dictionary-{}-{}.opf'.format(SRC_LANG, TRG_LANG).lower()
    if VERBOSE: print("Opf: {}".format(fname))
    with open(fname, 'w') as to:
        to.write("""<?xml version="1.0" encoding="UTF-8"?>

<package version="2.0" xmlns="http://www.idpf.org/2007/opf" unique-identifier="uid">

<metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">
    <dc:identifier id="uid">{uuid}</dc:identifier>
    <dc:title>{title}</dc:title>
    <dc:language>{source}</dc:language>
    <x-metadata>
        <DictionaryInLanguage>{source}</DictionaryInLanguage>
        <DictionaryOutLanguage>{target}</DictionaryOutLanguage>
    </x-metadata>
</metadata>

<manifest>""".format(uuid=uuid.uuid4(), source=SRC_LANG, target=TRG_LANG, title=DICT_TITLE))

        yield to

        to.write("""
<guide>
    <reference type="search" title="Dictionary Search" onclick= "index_search()"/>
</guide>

</package>
""")


# Write the opf that describes all the key files
def writeopf(ndicts, name):
    with openopf() as to:
        for i in range(ndicts):
            to.write("""
    <item id="dictionary{ndict}" href="dictionary-{src}-{trg}-{ndict}.html" media-type="application/xhtml+xml"/>""".format(ndict=i, src=SRC_LANG, trg=TRG_LANG))

        to.write("""
</manifest>

<spine>""")
        for i in range(ndicts):
            to.write("""
    <itemref idref="dictionary{ndict}"/>""".format(ndict=i))

        to.write("""
</spine>
""")


######################################################
# main
######################################################

print("Reading keys…")
defns = readkeys()
name = os.path.splitext(os.path.basename(TAB_FILE))[0]

print("Writing keys…")
ndicts = writekeys(defns)

print("Writing opf…")
writeopf(ndicts, name)

print("Done.")
