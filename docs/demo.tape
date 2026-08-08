Output docs/assets/workflow-demo.gif

Set Shell "zsh"
Set FontSize 16
Set Width 1100
Set Height 720
Set TypingSpeed 45ms
Set PlaybackSpeed 1
Set Theme { "name": "Whimsy", "background": "#171b24", "foreground": "#f5f7fb", "selection": "#36d6e7", "cursor": "#d946ef" }

Hide
Type "clear && X_UNFOLLOW_HOME=/tmp/x-unfollow-demo .venv/bin/python scripts/run_demo.py"
Enter
Sleep 1s
Show
Sleep 2s

# Configure the activity rule, scan size, budget, and safety limits.
Type "5"
Sleep 2s
Type "180"
Enter
Sleep 1s
Type "3"
Enter
Sleep 1s
Type "0.10"
Enter
Sleep 1s
Type "5"
Enter
Sleep 1s
Type "24"
Enter
Sleep 3s
Enter
Sleep 2s

# Run the local staged scan and leave its completion screen visible.
Type "1"
Sleep 2s
Type "y"
Enter
Sleep 5s
Enter
Sleep 2s

# Inspect the candidate before marking it.
Type "2"
Sleep 4s
Type "u"
Enter
Sleep 3s
Enter
Sleep 2s

# Preview first; this performs no change.
Type "3"
Sleep 4s
Enter
Sleep 2s

# Execute only after the final confirmation.
Type "4"
Sleep 3s
Type "y"
Enter
Sleep 4s
Enter
Sleep 3s

Hide
Type "q"
