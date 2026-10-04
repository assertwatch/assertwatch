# Records docs/demo/demo.gif and the poster docs/demo/demo.png. Run from the repository root:
#   vhs docs/demo/demo.tape
# The GIF opens on the findings (so its first frame and the poster show the result), then
# replays how it got there.
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
Type "# the agent's change to make the tests pass:"
Enter
Type "git diff -U1 --color test_prices.py"
Enter
Type "assertwatch check --worktree"
Enter
Sleep 2s
Show
Sleep 3s
Screenshot docs/demo/demo.png
Sleep 1s

Hide
Type "clear"
Enter
Sleep 500ms
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
