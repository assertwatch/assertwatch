# Records docs/demo/demo.gif. Run from the repository root: vhs docs/demo/demo.tape
Output docs/demo/demo.gif

Set Shell "bash"
Set FontSize 14
Set Width 1240
Set Height 640
Set Padding 20
Set Theme "GitHub Dark"
Set TypingSpeed 45ms

Hide
Type "export PATH=$PWD/.venv/bin:$PATH PS1='$ ' GIT_PAGER=cat && docs/demo/setup.sh /tmp/aw-demo && cd /tmp/aw-demo && clear"
Enter
Sleep 1s
Show

Type "# the agent's change to make the tests pass:"
Sleep 500ms
Enter
Type "git diff -U1 --color test_prices.py"
Sleep 400ms
Enter
Sleep 4s

Type "assertwatch check --worktree"
Sleep 400ms
Enter
Sleep 7s
