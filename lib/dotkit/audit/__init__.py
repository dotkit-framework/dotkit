"""dotkit audit: the known vulnerabilities and malicious releases of the tools that dotkit installs.

The data comes from OSV.dev (https://osv.dev). The modules:

  inventory.py  the tools and their versions: Nix, mise, Homebrew, and the packages of dotkit itself
  match.py      the decisions: the OSV queries of a tool, the advisories, the ignore rules. Runs no command
  osv.py        the OSV API, with curl
  issues.py     one GitHub issue per finding, with gh
  cli.py        the command: the report, the JSON, the notification
"""
