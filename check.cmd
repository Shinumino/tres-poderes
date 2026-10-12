@echo off
rem The one command that says whether a change is acceptable. CI will run exactly this,
rem so "it passed on my machine" and "it passed in CI" cannot mean different things.
rem
rem   check.cmd                       everything
rem   check.cmd -k centavos           only tests whose name matches
cd /d "%~dp0"
python -m unittest discover -s tests -t . %*
exit /b %errorlevel%
