#!/usr/bin/env bash
# Installs mpv-anilist-updater into mpv's scripts folder. Run it again to update.
zip_url="${ANILIST_UPDATER_ZIP:-https://github.com/AzuredBlue/mpv-anilist-updater/archive/refs/heads/main.zip}"
token_url="https://anilist.co/api/v2/oauth/authorize?client_id=20740&response_type=token"
user_files=(anilistToken.txt cache.json queue.json anilistUpdater.conf)

tmp=
old_version_moved=

fail() {
	echo "Error: $1" >&2
	exit 1
}

# Runs on every exit: put the old version back if the install did not finish.
cleanup() {
	local status=$?
	if [ -n "$old_version_moved" ] && [ "$status" -ne 0 ]; then
		echo "Restoring the previous version..." >&2
		rm -rf "$target"
		mv "$backup" "$target"
	fi
	[ -n "$tmp" ] && rm -rf "$tmp"
	exit "$status"
}
trap cleanup EXIT

find_config_dir() {
	if [ -n "${MPV_CONFIG_DIR:-}" ]; then
		echo "$MPV_CONFIG_DIR"
		return
	fi
	case "$(uname)" in
	Linux)
		# A package's data folder existing means mpv is installed that way
		if [ -d "$HOME/.var/app/io.mpv.Mpv" ]; then
			echo "$HOME/.var/app/io.mpv.Mpv/config/mpv"
		elif [ -d "$HOME/snap/mpv" ]; then
			echo "$HOME/snap/mpv/current/.config/mpv"
		elif [ -d "$HOME/snap/mpv-wayland" ]; then
			echo "$HOME/snap/mpv-wayland/common/.config/mpv"
		else
			echo "${XDG_CONFIG_HOME:-$HOME/.config}/mpv"
		fi
		;;
	Darwin) echo "$HOME/.config/mpv" ;;
	*) return 1 ;;
	esac
}

update_git_clone() {
	command -v git >/dev/null || fail "git is needed to update a git clone."
	local branch
	branch="$(git -C "$target" rev-parse --abbrev-ref HEAD)"
	[ "$branch" = main ] || fail "The clone is on branch '$branch'. Run 'git checkout main' in $target and try again."
	git -C "$target" pull --ff-only ||
		fail "git pull failed. Commit or discard your changes in $target and try again."
	# Hide the development files; `git sparse-checkout disable` brings them back
	git -C "$target" sparse-checkout set --no-cone '/*' '!/tests/' '!/.github/' '!/installers/' '!/ruff.toml' ||
		echo "Warning: couldn't hide the development files (needs a recent git)."
	result="updated (git)"
}

install_from_zip() {
	local name
	for name in curl unzip; do
		command -v "$name" >/dev/null || fail "Missing dependency: $name"
	done

	tmp="$(mktemp -d)" || fail "Couldn't create a temporary directory."
	echo "Downloading..."
	curl -fsSL -o "$tmp/source.zip" "$zip_url" || fail "Couldn't download $zip_url"
	unzip -q -d "$tmp/source" "$tmp/source.zip" || fail "Couldn't extract the download."
	local new_version
	new_version="$(find "$tmp/source" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
	[ -f "$new_version/main.lua" ] || fail "The download doesn't contain the script."

	mkdir -p "$(dirname "$target")" || fail "Couldn't create $(dirname "$target")."
	result="installed"
	if [ -e "$target" ]; then
		result="updated"
		rm -rf "$backup"
		mv "$target" "$backup" || fail "Couldn't move $target out of the way."
		old_version_moved=1
	fi
	mv "$new_version" "$target" || fail "Couldn't move the new version into $target."

	local file
	for file in "${user_files[@]}"; do
		if [ -e "$backup/$file" ]; then
			cp -p "$backup/$file" "$target/$file" || fail "Couldn't keep $file."
		fi
	done
	rm -rf "$backup"
	old_version_moved=
}

ask_for_token() {
	echo
	echo "The script needs an AniList token. Authorize the app here and copy the token:"
	echo "$token_url"
	local token=
	if [ -t 0 ]; then
		read -r -p "Paste the token (Enter to skip): " token
		token="${token//[[:space:]]/}"
	fi
	if [ -n "$token" ]; then
		(umask 077 && echo "$token" >"$target/anilistToken.txt")
	else
		echo "Put the token in $target/anilistToken.txt before using the script."
	fi
}

config_dir="$(find_config_dir)" || fail "This installer supports Linux and macOS only."
target="$config_dir/scripts/mpv-anilist-updater"
backup="$config_dir/.anilist-updater-backup"
echo "Installing into $target"

if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
	echo "Warning: Python 3.11 or newer was not found as python3. The script needs it to run."
fi

if [ -d "$target/.git" ]; then
	update_git_clone
else
	install_from_zip
fi

if [ ! -f "$target/anilistToken.txt" ]; then
	ask_for_token
fi

echo "mpv-anilist-updater has been $result."
