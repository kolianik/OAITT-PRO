@echo off
echo Preparing OAITT-PRO Environment...

IF NOT EXIST "vendor\gigaam\gigaam" (
    echo Cloning GigaAM submodule...
    git clone https://github.com/salute-developers/GigaAM.git vendor/gigaam
)

python prepare.py
pause
