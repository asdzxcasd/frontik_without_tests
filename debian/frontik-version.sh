#!/bin/sh

version=`dpkg-parsechangelog | grep -m 1 "^Version:" | sed "s/^Version: \(.*\)/\1/"`
sed -i "s/DEVELOPMENT/$version/" debian/frontik/usr/share/pyshared/frontik/version.py
