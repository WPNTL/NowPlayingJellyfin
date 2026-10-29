# bash TAB completion for the `display` command (NowPlayingJellyfin)
#
# install.sh drops this in /usr/share/bash-completion/completions/display, bash-completion
# picks it up on its own. works w/ `sudo display <TAB>` too.
#
# it asks `display __complete` what's valid instead of keeping a list in here,
# so new/custom themes just show up

_np_display() {
    local cur=${COMP_WORDS[COMP_CWORD]}
    local opts
    # everything typed after `display`, minus the word being completed
    opts=$(display __complete "${COMP_WORDS[@]:1:COMP_CWORD-1}" 2>/dev/null) || return 0
    mapfile -t COMPREPLY < <(compgen -W "$opts" -- "$cur")
}

complete -F _np_display display
