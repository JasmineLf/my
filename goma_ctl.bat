@echo off
:: Copyright 2019 The Chromium Authors. All rights reserved.
:: Use of this source code is governed by a BSD-style license that can be
:: found in the LICENSE file.

:: See revert instructions in cipd_manifest.txt

@call "%~dp0\cipd_bin_setup.bat" > nul 2>&1
@call vpython.bat %~dp0\.cipd_bin\goma_ctl.py %*
exit /b
