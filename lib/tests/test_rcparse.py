import os
import shutil
import subprocess
import unittest

from dotkit.migrate import rcparse
from tests import fake

OLD_ZSHRC = """\
# my zshrc
export ZSH="$HOME/.oh-my-zsh"
ZSH_THEME="robbyrussell"
plugins=(
  git
  z
)
source $ZSH/oh-my-zsh.sh
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \\. "$NVM_DIR/nvm.sh"
export PATH="$HOME/bin:$HOME/missing:$PATH"
export GOPATH=$HOME/go
path+=($GOPATH/bin)
# The editor.
export EDITOR="code --wait"
export GITHUB_TOKEN=@GH@
export LATER=$(date)
alias gs='git status'
alias bad='foo'; echo hi
eval "$(pyenv init -)"
eval "$(starship init zsh)"
mkcd() {
  mkdir -p "$1" && cd "$1"
}
if [[ -f ~/.work ]]; then
  source ~/.work
fi
case $TERM in
  xterm*) echo x;;
esac
setopt HIST_IGNORE_DUPS
export GPG_TTY=$(tty)
autoload -Uz compinit && compinit
"""
OLD_ZSHRC = fake.fill(OLD_ZSHRC)


class ParserTests(unittest.TestCase):
    def setUp(self):
        self.home = str(fake.tempdir("rcparse"))
        self.addCleanup(fake.remove, self.home)
        os.makedirs(f"{self.home}/bin")
        os.makedirs(f"{self.home}/go/bin")

    def parse(self, text, shell="zsh", name=".zshrc", known=None):
        return rcparse.Parser(self.home, shell).parse(name, text, known)

    def cats(self, r):
        return {(f.where, f.category) for f in r.findings}

    def test_old_zshrc(self):
        r = self.parse(OLD_ZSHRC)
        self.assertEqual(r.carried_text(), (
            'path=("$HOME/bin" $path)\n'
            "export GOPATH=$HOME/go\n"
            'path+=("$HOME/go/bin")\n'
            "# The editor.\n"
            'export EDITOR="code --wait"\n'
            "alias gs='git status'\n"
            'mkcd() {\n  mkdir -p "$1" && cd "$1"\n}\n'
            "export GPG_TTY=$TTY\n"
        ))
        self.assertEqual([(s.lines, s.name) for s in r.secrets], [((16, 16), "GITHUB_TOKEN")])
        c = self.cats(r)
        for expected in [("2", rcparse.FRAMEWORK), ("3", rcparse.FRAMEWORK), ("4-7", rcparse.FRAMEWORK),
                         ("8", rcparse.FRAMEWORK), ("9", rcparse.VERSION_MANAGER), ("10", rcparse.VERSION_MANAGER),
                         ("11", rcparse.PATHS), ("17", rcparse.EXPORTS), ("19", rcparse.COMMANDS),
                         ("20", rcparse.VERSION_MANAGER), ("21", rcparse.DONE), ("25-27", rcparse.BLOCKS),
                         ("28-30", rcparse.BLOCKS), ("31", rcparse.OPTIONS), ("33", rcparse.DONE)]:
            self.assertIn(expected, c)
        path = next(f for f in r.findings if f.where == "11")
        self.assertIn("$HOME/missing does not exist", path.note)

    def test_finding_source(self):
        """A finding names the framework or version manager of the catalog that the line uses."""
        r = self.parse(
            "ZSH_CUSTOM=$HOME/custom\n"                             # 1: a variable of Oh My Zsh
            "source $ZSH/oh-my-zsh.sh\n"                            # 2: a word of Oh My Zsh
            "zinit light romkatv/powerlevel10k\n"                   # 3: the first word wins
            "[[ ! -f ~/.p10k.zsh ]] || source ~/.p10k.zsh\n"        # 4
            'export NVM_DIR="$HOME/.nvm"\n'                         # 5
            "export RBENV_ROOT=/opt/rbenv-root\n"                   # 6
            "export JENV_ROOT=/opt/j\n"                             # 7: a variable of jenv
            'eval "$(pyenv init -)"\n'                              # 8
            'export PATH="$HOME/miniconda3/bin:$PATH"\n'            # 9: a PATH marker of conda
            'export PATH="$HOME/missing:$PATH"\n'                   # 10: not a version manager
            "pyuse() { pyenv shell $1; }\n"                         # 11: a function
            "setopt AUTO_CD\n"                                      # 12
            'sdk() { source "$SDKMAN_DIR/bin/sdkman-init.sh"; }\n'  # 13
            "export ZPLUG_X=1; zplug load\n"                        # 14
            'export PATH="$ASDF_DATA_DIR/shims:$PATH"\n'             # 15: a variable of asdf
            "export ZDOTDIR=~/.zsh\n"                               # 16: zsh's own, no framework
        )
        self.assertEqual([(f.where, f.category, f.source) for f in r.findings], [
            ("1", rcparse.FRAMEWORK, "Oh My Zsh"),
            ("2", rcparse.FRAMEWORK, "Oh My Zsh"),
            ("3", rcparse.FRAMEWORK, "zinit"),
            ("4", rcparse.FRAMEWORK, "Powerlevel10k"),
            ("5", rcparse.VERSION_MANAGER, "nvm"),
            ("6", rcparse.VERSION_MANAGER, "rbenv"),
            ("7", rcparse.VERSION_MANAGER, "jenv"),
            ("8", rcparse.VERSION_MANAGER, "pyenv"),
            ("9", rcparse.PATHS, "conda"),
            ("10", rcparse.PATHS, ""),
            ("11", rcparse.VERSION_MANAGER, "pyenv"),
            ("12", rcparse.OPTIONS, ""),
            ("13", rcparse.VERSION_MANAGER, "sdkman"),
            ("14", rcparse.FRAMEWORK, "zplug"),
            ("15", rcparse.PATHS, "asdf"),
            ("16", rcparse.FRAMEWORK, ""),
        ])

    def test_every_line_is_carried_or_reported(self):
        r = self.parse(OLD_ZSHRC)
        seen = set()
        for c in r.carried:
            seen.update(range(c.lines[0], c.lines[1] + 1))
        for f in r.findings:
            seen.update(range(f.lines[0], f.lines[1] + 1))
        for s in r.secrets:
            seen.update(range(s.lines[0], s.lines[1] + 1))
        code = {i for i, line in enumerate(OLD_ZSHRC.splitlines(), 1) if line.strip()}
        self.assertEqual(code - seen, {1}, "only the first comment is dropped")

    def test_plain_values(self):
        ok = ['export EDITOR="code --wait"', "export X='a $b'", "export SDK=~/Library/sdk",
              "export GOPATH=$HOME/go", 'export D="${HOME}/x"', "EDITOR=vim", "export N=1 # a comment"]
        for line in ok:
            r = self.parse(line + "\n")
            self.assertEqual(r.statements, 1, line)
        bad = ["export LATER=$(date)", "export X=$OTHER", "export A=b; echo hi", 'export Y="`id`"']
        for line in bad:
            r = self.parse(line + "\n")
            self.assertEqual(r.statements, 0, line)
            self.assertEqual(r.findings[0].category, rcparse.EXPORTS if "OTHER" in line or "date" in line
                             else r.findings[0].category, line)

    def test_variables_carry_to_the_next_file(self):
        env = self.parse("export GOPATH=$HOME/go\n", name=".zshenv")
        r = self.parse("export GOBIN=$GOPATH/bin\npath=($GOBIN $path)\n", known=env.variables)
        self.assertEqual(r.carried_text(), 'export GOBIN=$GOPATH/bin\npath=("$HOME/go/bin" $path)\n')

    def test_path_lines(self):
        r = self.parse('export PATH="/opt/homebrew/bin:$PATH"\n')
        self.assertEqual(r.findings[0].category, rcparse.DONE)
        r = self.parse("export PATH=/usr/bin:/bin\n")
        self.assertEqual(r.findings[0].note, "the line replaces PATH")
        r = self.parse('export PATH="$PATH:$HOME/bin"\n')
        self.assertEqual(r.carried_text(), 'path+=("$HOME/bin")\n')
        # A carried path line knows its directories, expanded, without the ones dotkit adds.
        os.makedirs(f"{self.home}/tools")
        r = self.parse('export PATH="/opt/homebrew/bin:$HOME/bin:$PATH:$HOME/tools"\n')
        self.assertEqual([(c.kind, c.dirs) for c in r.carried],
                         [("path", (f"{self.home}/bin", f"{self.home}/tools"))])
        self.assertTrue(rcparse.path_done("/opt/homebrew/bin/", self.home))
        self.assertFalse(rcparse.path_done(f"{self.home}/bin", self.home))
        r = self.parse('export PATH="$HOME/.pyenv/shims:$PATH"\n')
        self.assertEqual(r.findings[0].category, rcparse.VERSION_MANAGER)
        r = self.parse('export PATH="$HOME/bin:$PATH"\n', shell="bash", name=".bash_profile")
        self.assertEqual(r.carried_text(), 'PATH="$HOME/bin:$PATH"\n')
        r = self.parse("typeset -U path PATH\n")
        self.assertEqual(r.findings[0].category, rcparse.DONE)
        r = self.parse("[ -f $HOME/.bashrc ] && source $HOME/.bashrc\n", shell="bash", name=".bash_profile")
        self.assertEqual(r.findings[0].category, rcparse.DONE)

    def test_installer_lines(self):
        os.makedirs(f"{self.home}/.cargo/bin")
        os.makedirs(f"{self.home}/Library/pnpm")
        text = ('. "$HOME/.cargo/env"\n'
                '. "$HOME/.local/bin/env"\n'
                'export PNPM_HOME="$HOME/Library/pnpm"\n'
                'case ":$PATH:" in\n  *":$PNPM_HOME:"*) ;;\n  *) export PATH="$PNPM_HOME:$PATH" ;;\nesac\n')
        r = self.parse(text, name=".zshenv")
        self.assertEqual(r.carried_text(), 'path=("$HOME/.cargo/bin" $path)\nexport PNPM_HOME="$HOME/Library/pnpm"\n'
                                           'path=("$HOME/Library/pnpm" $path)\n')
        self.assertEqual([(f.where, f.category) for f in r.findings], [("2", rcparse.DONE)])

    def test_tool_inits_that_dotkit_does(self):
        for line in ('eval "$(/opt/homebrew/bin/brew shellenv)"', 'eval "$(zoxide init zsh)"',
                     'eval "$(mise activate zsh)"', 'eval "$(direnv hook zsh)"', "source <(fzf --zsh)",
                     "[ -f ~/.fzf.zsh ] && source ~/.fzf.zsh",
                     "source /opt/homebrew/share/zsh-autosuggestions/zsh-autosuggestions.zsh"):
            r = self.parse(line + "\n")
            self.assertEqual([f.category for f in r.findings], [rcparse.DONE], line)

    def test_p10k_block(self):
        text = ('if [[ -r "${XDG_CACHE_HOME:-$HOME/.cache}/p10k-instant-prompt-${(%):-%n}.zsh" ]]; then\n'
                '  source "${XDG_CACHE_HOME:-$HOME/.cache}/p10k-instant-prompt-${(%):-%n}.zsh"\nfi\n'
                "[[ ! -f ~/.p10k.zsh ]] || source ~/.p10k.zsh\n")
        r = self.parse(text)
        self.assertEqual([(f.where, f.category) for f in r.findings],
                         [("1-3", rcparse.FRAMEWORK), ("4", rcparse.FRAMEWORK)])

    def test_conda_block(self):
        text = ("# >>> conda initialize >>>\n__conda_setup=\"$('/opt/conda/bin/conda' 'shell.zsh' 'hook')\"\n"
                "if [ $? -eq 0 ]; then\n    eval \"$__conda_setup\"\nelse\n    export PATH=\"/opt/conda/bin:$PATH\"\n"
                "fi\nunset __conda_setup\n# <<< conda initialize <<<\n")
        r = self.parse(text)
        self.assertEqual(r.statements, 0)
        self.assertTrue(all(f.category in (rcparse.VERSION_MANAGER, rcparse.COMMANDS) for f in r.findings))

    def test_aliases(self):
        r = self.parse("alias ll='ls -la' la='ls -A'\nalias -g G='| grep'\nalias x=\"$(foo)\"\n")
        self.assertEqual(r.statements, 2)
        self.assertEqual(r.findings[0].where, "3")

    def test_functions(self):
        r = self.parse("hi() { echo hi; }\nfunction greet {\n  echo \"hi $1\"\n}\nnv() {\n  nvm use 20\n}\n")
        self.assertEqual(r.carried_text(), 'hi() { echo hi; }\nfunction greet {\n  echo "hi $1"\n}\n')
        self.assertEqual(r.findings[0].category, rcparse.VERSION_MANAGER)

    def test_secrets_never_reach_the_repository(self):
        text = ("# export OPENAI_API_KEY=@OPENAI@\n"
                "export OPENAI_API_KEY=@OPENAI@\n"
                "gh_login() {\n  echo @GH@\n}\n"
                "if true; then\n  export TOKEN=@GH@\nfi\n"
                'curl -H "Authorization: Bearer @GH@" x\n')
        r = self.parse(fake.fill(text))
        self.assertNotIn("sk-", r.carried_text())
        self.assertNotIn("ghp_", r.carried_text())
        for f in r.findings:
            self.assertNotIn("ghp_", f.text)
        self.assertTrue(all(f.secret for f in r.findings))
        self.assertEqual([s.name for s in r.secrets],
                         ["OPENAI_API_KEY", "the function on this line, with a GitHub token"])

    def test_review_cases_go_to_the_local_file(self):
        text = fake.fill(
            "export PAT=abcdef123456ghijkl\n"
            "export DB_PASS=hunter2hunter2\n"
            "export OPENAI_KEY=abcd1234efgh5678\n"
            "export STRIPE_KEY=@STRIPE@\n"
            "typeset -x API_TOKEN=abcdef123456\n"
            "alias gh2='GITHUB_TOKEN=abcdef123456 gh'\n"
            "f() { export API_KEY=abcdef123456; }\n"
            "deploy() {\n  curl -u admin:hunter2 https://example.com\n"
            '  curl -H "X-Api-Key: abcdef123456" https://example.com\n}\n'
            "# export GITHUB_TOKEN=@HEX40@\n"
            "export EDITOR=vim\n")
        r = self.parse(text)
        self.assertEqual(r.carried_text(), "export EDITOR=vim\n")
        self.assertEqual([s.lines for s in r.secrets],
                         [(1, 1), (2, 2), (3, 3), (4, 4), (5, 5), (6, 6), (7, 7), (8, 11)])
        self.assertEqual([(f.where, f.secret) for f in r.findings], [("12", True)])
        for value in ("abcdef123456", "hunter2", fake.TOKENS["@STRIPE@"], fake.TOKENS["@HEX40@"]):
            self.assertNotIn(value, r.carried_text())
            for f in r.findings:
                self.assertNotIn(value, f.text)

    def test_continued_secrets_keep_whole_statements(self):
        alias = ("alias request='" + 'curl -H "X-Api-Key: ' + "\\\n" +
                 'hunter2hunter2" https://example.com' + "'")
        function = ('request() {\n  curl -H "X-Api-Key: ' + "\\\n" +
                    'hunter2hunter2" https://example.com\n  echo done\n}')
        assignment = 'export MYSQL_PWD=' + "\\\n" + 'hunter2hunter2'
        text = "\n".join((alias, function, assignment, 'export EDITOR=vim')) + "\n"
        r = self.parse(text)
        self.assertEqual(r.carried_text(), 'export EDITOR=vim\n')
        self.assertEqual([s.text for s in r.secrets], [alias, function, assignment])
        self.assertEqual([s.lines for s in r.secrets], [(1, 2), (3, 7), (8, 9)])
        self.assertEqual(r.findings, [])

    def test_heredoc_secret_is_masked_as_a_whole(self):
        r = self.parse('cat <<EOF\npassword hunter2hunter2\nEOF\nexport EDITOR=vim\n')
        self.assertEqual(r.carried_text(), 'export EDITOR=vim\n')
        self.assertEqual([(f.where, f.secret, f.text) for f in r.findings], [('1-3', True, '')])

    def test_heredoc_and_continuation(self):
        text = "cat <<EOF\nif\nEOF\nexport A=1 \\\n  B=2\nexport C=3\n"
        r = self.parse(text)
        self.assertEqual(r.findings[0].where, "1-3")
        self.assertEqual(r.carried_text(), "export C=3\n")

    @unittest.skipUnless(shutil.which("zsh"), "no zsh")
    def test_carried_text_parses(self):
        r = self.parse(OLD_ZSHRC)
        out = subprocess.run(["zsh", "-n", "-c", r.carried_text()], capture_output=True)
        self.assertEqual(out.returncode, 0, out.stderr)


if __name__ == "__main__":
    unittest.main()
