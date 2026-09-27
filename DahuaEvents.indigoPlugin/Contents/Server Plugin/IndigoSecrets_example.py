#! /usr/bin/env python
# -*- coding: utf-8 -*-
# Filename:    IndigoSecrets_example.py
# Description: Template for the camera username and password DahuaEvents reads from
#              IndigoSecrets.py. The plugin never imports this file; it is here to
#              copy.
#
#              To use it, copy this file into
#                  /Library/Application Support/Perceptive Automation/
#              rename the copy to IndigoSecrets.py, and put your values between the
#              quotes. If you already have an IndigoSecrets.py there (other plugins
#              use the same file), add the two lines below to it instead.
#
#              Anything set here wins over the username and password typed in
#              Plugins -> DahuaEvents -> Configure. Leave a value empty ("") and the
#              plugin uses the Configure dialog for it.
#
#              IndigoSecrets.py is listed in .gitignore and must never be committed.
# Author:      CliveS & Claude Opus 5.5
# Date:        27-09-2026
# Version:     1.0

# ============================
# Dahua / Amcrest cameras
# Required by: DahuaEvents (one account used for every camera)
# ============================
DAHUA_USER = ""     # camera account username
DAHUA_PASS = ""     # that account's password
