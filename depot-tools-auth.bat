@echo off
:: Copyright 2015 The Chromium Authors. All rights reserved.
:: Use of this source code is governed by a BSD-style license that can be
:: found in the LICENSE file.
setlocal

:: Ensure that "depot_tools" is somewhere in PATH so this tool can be used
:: standalone, but allow other PATH manipulations to take priority.
PATH=%PATH%;%~dp0

:: Defer control.
python "%~dp0\depot-tools-auth.py" %*
