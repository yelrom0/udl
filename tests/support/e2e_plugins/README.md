# The e2e fakes

`tests/support/e2e.py` loads this folder as udl's `plugin_dirs`, so every plugin the e2e tests
need is a local plugin: a `*.py` file that defines the plugin class (udl imports the file and
registers the classes it defines).

The fakes for the Gmail and Slack plugins' integrations land with the tasks that need them
(18.5's sign-in, 19's accounts), as `google.py`, `slack.py` and `ai.py` here.