# IRC!Radio

IRC!Radio is a radio station for IRC channels. You hang around
on IRC, adding YouTube songs to the bot, listening to it with
all your friends. Great fun!

### Stack

IRC!Radio aims to be minimalistic/small using:

- Python >= 3.7
- SQLite
- LiquidSoap >= 1.4.3
- Icecast2
- Quart web framework

## Ubuntu installation

No docker. The following assumes you have a VPS somewhere with root access.

#### 1. Requirements

As `root`:

```
apt install -y liquidsoap icecast2 nginx python3-certbot-nginx python3-virtualenv libogg-dev ffmpeg sqlite3
ufw allow 80
ufw allow 443
```

When the installation asks for icecast2 configuration, skip it.

#### 2. Create system user

As `root`:

```text
adduser radio
```

#### 2. Clone this project

As `radio`:

```bash
su radio
cd ~/

git clone https://git.wownero.com/dsc/ircradio.git
cd ircradio/
virtualenv -p /usr/bin/python3 venv
source venv/bin/activate

pip install -r requirements.txt
```

#### 3. Generate some configs

```bash
cp settings.py_example settings.py
```

Look at `settings.py` and configure it to your liking:

- Change `icecast2_hostname` to your hostname, i.e: `radio.example.com`
- Change `irc_host`, `irc_port`, `irc_channels`, and `irc_admins_nicknames`
- Change the passwords under `icecast2_`
- Change the `liquidsoap_description` to whatever

When you are done, execute this command:

```bash
python run generate
```

This will write icecast2/liquidsoap/nginx configuration files into `data/`.

#### 4. Applying configuration

As `root`, copy the following files:

```bash
cp data/icecast.xml /etc/icecast2/
cp data/liquidsoap.service /etc/systemd/system/
cp data/radio_nginx.conf /etc/nginx/sites-enabled/
```

#### 5. Starting some stuff

As `root` 'enable' icecast2/liquidsoap/nginx, this is to
make sure these applications start when the server reboots.

```bash
sudo systemctl enable liquidsoap
sudo systemctl enable nginx
sudo systemctl enable icecast2
```

And start them:

```bash
sudo systemctl start icecast2
sudo systemctl start liquidsoap
```

Reload & start nginx:

```bash
systemctl reload nginx
sudo systemctl start nginx
```

### 6. Run the webif and IRC bot:

As `radio`, issue the following command:

```bash
python3 run webdev
```

Run it in `screen` or `tux` to keep it up, or write a systemd unit file for it.

### 7. Generate HTTPs certificate

```bash
certbot --nginx
```

Pick "Yes" for redirects.

## Command list

```text
- !np         - current song
- !tune       - upvote song
- !boo        - downvote song
- !request    - search and queue a song by title
- !dj+        - add a YouTube ID to the radiostream
- !dj-        - remove a YouTube ID
- !ban+       - ban a YouTube ID and/or nickname
- !ban-       - unban a YouTube ID and/or nickname
- !skip       - skips current song
- !listeners  - show current amount of listeners
- !queue      - show queued up music
- !queue_user - queue a random song by user
- !search     - search for a title
- !stats      - stats
```
