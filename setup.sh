#!/bin/bash
termux-setup-storage && yes | pkg update && yes | pkg upgrade && yes | pkg i python && yes | pkg install python-pip && pkg install python tsu libexpat openssl -y && curl -Ls "https://raw.githubusercontent.com/Garnt024020806/Free-Tool-Rejoin-Ug-open-source/main/requirements.txt" -o "$HOME/requirements.txt" && pip install -r "$HOME/requirements.txt"
curl -Ls "https://raw.githubusercontent.com/Garnt024020806/Free-Tool-Rejoin-Ug-open-source/main/rejoin_en.py" -o /sdcard/Download/rejoin_en.py && cp /sdcard/Download/rejoin_en.py "$HOME/rejoin_en.py"
