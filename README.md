# kodi-to-jellyfin

I wrote this to import my play counts from Kodi to Jellyfin.

I cobbled this together from the breadcrumbs I found on [this Reddit
thread](https://www.reddit.com/r/jellyfin/comments/irlepz/comment/gbz00m2/).

## Setup

You'll need a relatively modern installation of Python. If you use Nix, see `.envrc`.

Grab an export of your Kodi watched data. For me, I did something like this:

    ssh [MACHINE-WITH-ACCESS-TO-KODI-DB] 'mysql MyVideos131 --batch -e "select strPath,strFileName,lastPlayed,playCount from path inner join files using(idPath) WHERE playCount >= 1"' > in/kodi-dump.tsv

Stop Jellyfin. Copy your Jellyfin data directory locally, and make a copy of it
(the next step will update the data in `./out/`, but it won't touch the stuff
in `./in/`):

    rsync -avP --rsync-path="sudo rsync" [MACHINE-WITH-JELLYFIN-DATA]:/var/lib/jellyfin/data/ in/jellyfin-data
    cp -r in/jellyfin-data out/jellyfin-data

## Usage

Pretty simple:

    ./kodi2jellyfin.py --jellyfin-username=[USERNAME] in/kodi-dump.tsv out/jellyfin-data

When done, move this directory back to your machine that runs Jellyfin.
(Jellyfin is still stopped, right?) Make sure you get the file permissions right.

Start Jellyfin back up. Did everything work?

- If yes, happy watching!
- If no, please file an issue.
