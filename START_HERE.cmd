@echo off
pip install -r requirements.txt
python build\make_singlefile.py
python dist\earcrate.py
