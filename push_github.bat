@echo off
cd E:\Personal_Projects\Kaizen
git remote add origin https://github.com/NKKesharwani/KaizenApp.git
git add -A
git commit -m "Initial commit of Kaizen project"
git push -u origin master
echo "Push complete"