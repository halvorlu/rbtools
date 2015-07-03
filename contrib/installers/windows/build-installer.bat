::
:: Build the installer for RBTools for Windows.
::
:: This will fetch Portable Python, prepare a slimmed-down build, and
:: build a WiX package for RBTools.
::
:: This can be run in an automated fashion, but it must be run manually the
:: first time in order to handle the installation of Portable Python (since
:: there's no way to do a minimal silent install).
::
@echo off
setlocal


::-------------------------------------------------------------------------
:: Build paths
::-------------------------------------------------------------------------

:: Store out the absolute path to the tree root.
pushd ..\..\..
set TREE_ROOT=%CD%
popd

set BUILD_DEST=%TREE_ROOT%\dist
set BUILD_BASE=%TREE_ROOT%\build\windows-pkg
set BUILD_ROOT=%BUILD_BASE%\build
set BUILD_STAGE=%BUILD_BASE%\stage
set DEPS_DIR=%BUILD_BASE%\deps


::-------------------------------------------------------------------------
:: Binaries
::-------------------------------------------------------------------------
set BUNDLED_PYTHON_DIR=%BUILD_ROOT%\Python27
set BUNDLED_PYTHON=%BUNDLED_PYTHON_DIR%\python.exe

call :SetMSBuildPath || goto :Abort


::-------------------------------------------------------------------------
:: Dependencies
::-------------------------------------------------------------------------
set PORTABLE_PYTHON_VERSION=2.7.6.1
set PORTABLE_PYTHON_URL=http://ftp.osuosl.org/pub/portablepython/v2.7/PortablePython_%PORTABLE_PYTHON_VERSION%.exe
set PORTABLE_PYTHON_MD5=5b480c1bbbc06b779a7d150c26f2147d
set PORTABLE_PYTHON_DEP=%DEPS_DIR%\PortablePython-%PORTABLE_PYTHON_VERSION%


::-------------------------------------------------------------------------
:: Signing certificate
::-------------------------------------------------------------------------
set CERT_THUMBPRINT=1d78bb47e6a8fc599ad61c639dc31048177b3800


::-------------------------------------------------------------------------
:: Begin the installation process
::-------------------------------------------------------------------------
if not exist "%DEPS_DIR%" mkdir "%DEPS_DIR%"

call :InstallPortablePython || goto :Abort
call :CreateBuildDirectory || goto :Abort
call :InstallRBTools || goto :Abort
call :RemoveUnwantedFiles || goto :Abort
call :BuildInstaller || goto :Abort

echo Done.

goto :EOF


::-------------------------------------------------------------------------
:: Installs and trims the Portable Python dependency
::-------------------------------------------------------------------------
:InstallPortablePython
setlocal

echo.
echo == Installing Portable Python ==

set _PORTABLE_PYTHON_INSTALLER=%TEMP%\PortablePython-%PORTABLE_PYTHON_VERSION%.exe

if not exist "%PORTABLE_PYTHON_DEP%" (
    if not exist "%_PORTABLE_PYTHON_INSTALLER%" (
        echo Downloading Portable Python v%PORTABLE_PYTHON_VERSION%...
        call :DownloadAndVerify %PORTABLE_PYTHON_URL% ^
                                "%_PORTABLE_PYTHON_INSTALLER%" ^
                                %PORTABLE_PYTHON_MD5% || exit /B 1

        echo Downloaded to %_PORTABLE_PYTHON_INSTALLER%
    )

    echo Running the installer...
    echo.
    echo **PAY ATTENTION**
    echo.
    echo You need to use the default destination path for the installer, and you
    echo must choose the "Minimum" install type.
    echo.

    start "" /wait "%_PORTABLE_PYTHON_INSTALLER%" /D=%PORTABLE_PYTHON_DEP%

    if ERRORLEVEL 1 exit /B 1

    del /F /Q "%_PORTABLE_PYTHON_INSTALLER%"
)

goto :EOF


::-------------------------------------------------------------------------
:: Populates the build directory from dependencies
::-------------------------------------------------------------------------
:CreateBuildDirectory
setlocal

:: Create a copy of the Portable Python directory. This is where we'll be
:: installing RBTools and dependencies, and what we'll actually be
:: distributing.
echo.
echo == Creating build directory ==

call :DeleteIfExists "%BUILD_ROOT%"
xcopy /EYI "%PORTABLE_PYTHON_DEP%\App" "%BUNDLED_PYTHON_DIR%" >NUL

goto :EOF


::-------------------------------------------------------------------------
:: Install RBTools and all dependencies
::-------------------------------------------------------------------------
:InstallRBTools
setlocal

echo.
echo == Installing RBTools and dependencies ==
echo.
echo --------------------------- [Install log] ---------------------------

pushd %TREE_ROOT%
"%BUNDLED_PYTHON%" setup.py release install >NUL

if ERRORLEVEL 1 (
    popd
    exit /B 1
)

popd

echo ---------------------------------------------------------------------

goto :EOF


::-------------------------------------------------------------------------
:: Remove unwanted files from the build directory.
::-------------------------------------------------------------------------
:RemoveUnwantedFiles
setlocal

echo.
echo == Removing unwanted files ==

call :DeleteIfExists "%BUNDLED_PYTHON_DIR%\Doc"
call :DeleteIfExists "%BUNDLED_PYTHON_DIR%\tcl"

goto :EOF


::-------------------------------------------------------------------------
:: Build the installer
::-------------------------------------------------------------------------
:BuildInstaller
setlocal

echo.
echo == Building the RBTools installer ==

call :GetRBToolsVersion
set _rbtools_version=%_return1%

set _wix_path=%CD%\wix

%MSBUILD% ^
    /p:Version="%_rbtools_version%" ^
    /p:Root="%BUILD_ROOT%" ^
    /p:OutputPath="%BUILD_STAGE%\\" ^
    /p:SourcePath="%_wix_path%" ^
    /p:CertificateThumbprint=%CERT_THUMBPRINT% ^
    /p:TimestampUrl=http://timestamp.comodoca.com/authenticode ^
    "%_wix_path%\rbtools.sln"

if ERRORLEVEL 1 exit /B 1

mkdir "%BUILD_DEST%" 2>&1
copy "%BUILD_STAGE%\RBTools-*.exe" "%BUILD_DEST%" >NUL

echo Installer published to %BUILD_DEST%

goto :EOF


::-------------------------------------------------------------------------
:: Returns the Python version for RBTools.
::
:: This must be run after installing RBTools in %BUILD_ROOT%.
::-------------------------------------------------------------------------
:GetRBToolsVersion
setlocal

set _version_file=%BUILD_STAGE%\VERSION

"%BUNDLED_PYTHON%" scripts/get-version.py > "%_version_file%"
set /P _version= < "%_version_file%"
del "%_version_file%"

endlocal & set _return1=%_version%
goto :EOF


::-------------------------------------------------------------------------
:: Determines the path to MSBuild.exe
::-------------------------------------------------------------------------
:SetMSBuildPath
setlocal

set _reg_key=HKLM\SOFTWARE\Microsoft\MSBuild\ToolsVersions\4.0
set _reg_query_cmd=reg.exe query "%_reg_key%" /V MSBuildToolsPath

%_reg_query_cmd% >NUL 2>&1

if ERRORLEVEL 1 (
    echo Cannot obtain the MSBuild tools path from the registry.
    exit /B 1
)

for /f "skip=2 tokens=2,*" %%A in ('%_reg_query_cmd%') do (
    SET MSBUILDDIR=%%B
)

if not exist %MSBUILDDIR%nul (
    echo The MSBuild tools path from the registry does not exist.
    echo
    echo The missing path is: %MSBUILDDIR%
    exit /B 1
)

if not exist %MSBUILDDIR%msbuild.exe (
    echo MSBuild.exe is missing from %MSBUILDDIR%.
    exit /B 1
)

endlocal & set MSBUILD=%MSBUILDDIR%msbuild.exe
goto :EOF


::-------------------------------------------------------------------------
:: Downloads and verifies a file from a URL.
::-------------------------------------------------------------------------
:DownloadAndVerify url dest expected_hash
setlocal

set _url=%~1
set _dest=%~2
set _expected_hash=%~3

if not exist "%_dest%" (
    call :DownloadFile %_url% "%_dest%" || exit /B 1
)

call :VerifyMD5 "%_dest%" %_expected_hash% || exit /B 1

goto :EOF


::-------------------------------------------------------------------------
:: Downloads a file from a URL to a given destination.
::-------------------------------------------------------------------------
:DownloadFile url dest
setlocal

set _url=%~1
set _dest=%~2

PowerShell -Command ^
    "(New-Object Net.WebClient).DownloadFile('%_url%', '%_dest%')"

if ERRORLEVEL 1 exit /B 1

goto :EOF


::-------------------------------------------------------------------------
:: Verifies the MD5 checksum of a file.
::-------------------------------------------------------------------------
:VerifyMD5 filename expected_hash
setlocal

set _filename=%~1
set _expected_hash=%~2

PowerShell -Command ^
 "$md5 = New-Object Security.Cryptography.MD5CryptoServiceProvider;"^
 "$file = [System.IO.File]::ReadAllBytes('%_filename%');"^
 "$hash = [System.BitConverter]::ToString($md5.ComputeHash($file));"^
 "$hash = $hash.toLower().Replace('-', '');"^
 "if ($hash -eq '%_expected_hash%') {"^
 "    exit 0;"^
 "} else {"^
 "    Write-Host 'Invalid checksum for %_filename%.';"^
 "    Write-Host 'Got'$hash'; expected %_expected_hash%.';"^
 "    exit 1;"^
 "}"

if ERRORLEVEL 1 exit /B 1

goto :EOF


::-------------------------------------------------------------------------
:: Deletes a file or directory if it exists.
::-------------------------------------------------------------------------
:DeleteIfExists path
setlocal

set _path=%~1

if exist "%_path%" (
    del /F /Q "%_path%" 2>NUL
    rmdir /S /Q "%_path%" 2>NUL
)

goto :EOF


::-------------------------------------------------------------------------
:: Aborts the creation of the installer.
::-------------------------------------------------------------------------
:Abort

echo Installation aborted.
exit /B 1
