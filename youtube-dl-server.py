from __future__ import unicode_literals
import json
import os
import subprocess
import glob
from queue import Queue
from bottle import route, run, Bottle, request, static_file, HTTPResponse
from threading import Thread
import youtube_dl
from pathlib import Path
from collections import ChainMap

app = Bottle()


app_defaults = {
    'YDL_FORMAT': 'bestvideo+bestaudio/best',
    'YDL_EXTRACT_AUDIO_FORMAT': None,
    'YDL_EXTRACT_AUDIO_QUALITY': '192',
    'YDL_RECODE_VIDEO_FORMAT': None,
    'YDL_OUTPUT_TEMPLATE': f'./static/%(id)s.%(ext)s',
    'YDL_ARCHIVE_FILE': None,
    'YDL_SERVER_HOST': '0.0.0.0',
    'YDL_SERVER_PORT': 8080,
}


@app.route('/youtube-dl/static/:filename#.*#', method='HEAD')
def server_static_info(filename):
    # check if file extension is omitted
    if len(os.path.splitext(filename)[1]) == 0:
        # use wildcard on file extension
        filename += '.*'
    for file in glob.glob(f'./static/{filename}'):
        ext = os.path.splitext(os.path.basename(file))
        # ignore .part file, which contains two dot in file path
        if ext[0].find('.') > -1:
            continue
        return HTTPResponse(status=200, headers={'X-Extension': ext[1]})
    return HTTPResponse(status=404)


@app.route('/youtube-dl/static/:filename#.*#', method='GET')
def server_static(filename):
    return static_file(filename, root='./static')


@app.route('/youtube-dl/q', method='GET')
def q_size():
    return HTTPResponse(status=200, body={"size": json.dumps(list(dl_q.queue))})


@app.route('/youtube-dl/q', method='POST')
def q_put():
    url = request.forms.get("url")
    options = {
        'format': request.forms.get("format"),
    }

    if not url:
        return HTTPResponse(status=400, body={"error": "/q called without a 'url' query param"})

    ydl_opt = get_ydl_options(options)
    dl_q.put((url, ydl_opt))
    print("Added url " + url + " to the download queue")
    duration = -1
    with youtube_dl.YoutubeDL(ydl_opt) as ydl:
        info = ydl.extract_info(url, download=False)
        duration = info['duration']
    return HTTPResponse(status=200, body={"url": url, "options": options, "duration": duration})


@app.route("/youtube-dl/update", method="GET")
def update():
    command = ["pip", "install", "--upgrade", "youtube-dl"]
    proc = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    output, error = proc.communicate()
    return {
        "output": output.decode('ascii'),
        "error":  error.decode('ascii')
    }


@app.route("/youtube-dl/progress", method="GET")
def getProgress():
    return progress.__dict__


def dl_worker():
    while not done:
        url, options = dl_q.get()
        download(url, options)
        dl_q.task_done()


def get_ydl_options(request_options):
    request_vars = {
        'YDL_EXTRACT_AUDIO_FORMAT': None,
        'YDL_RECODE_VIDEO_FORMAT': None,
    }

    requested_format = request_options.get('format', 'bestvideo')

    if requested_format in ['aac', 'flac', 'mp3', 'm4a', 'opus', 'vorbis', 'wav']:
        request_vars['YDL_EXTRACT_AUDIO_FORMAT'] = requested_format
    elif requested_format == 'bestaudio':
        request_vars['YDL_EXTRACT_AUDIO_FORMAT'] = 'best'
    elif requested_format in ['mp4', 'flv', 'webm', 'ogg', 'mkv', 'avi']:
        request_vars['YDL_RECODE_VIDEO_FORMAT'] = requested_format

    ydl_vars = ChainMap(request_vars, os.environ, app_defaults)

    postprocessors = []

    if(ydl_vars['YDL_EXTRACT_AUDIO_FORMAT']):
        postprocessors.append({
            'key': 'FFmpegExtractAudio',
            'preferredcodec': ydl_vars['YDL_EXTRACT_AUDIO_FORMAT'],
            'preferredquality': ydl_vars['YDL_EXTRACT_AUDIO_QUALITY'],
        })

    if(ydl_vars['YDL_RECODE_VIDEO_FORMAT']):
        postprocessors.append({
            'key': 'FFmpegVideoConvertor',
            'preferedformat': ydl_vars['YDL_RECODE_VIDEO_FORMAT'],
        })

    return {
        'format': ydl_vars['YDL_FORMAT'],
        'postprocessors': postprocessors,
        'outtmpl': ydl_vars['YDL_OUTPUT_TEMPLATE'],
        'download_archive': ydl_vars['YDL_ARCHIVE_FILE'],
        'quiet': True
    }


def download(url, request_options):
    try:
        with youtube_dl.YoutubeDL(request_options) as ydl:
            # Refer to: https://docs.python.org/3/faq/library.html#what-kinds-of-global-value-mutation-are-thread-safe
            # It's thread-safe on a simple assignment
            global progress
            progress = Progress(url, request_options)
            ydl.add_progress_hook(progress.update)
            ydl.download([url])
    except:
        pass


class Progress:
    url = ""
    opt = {}
    finished = False
    downloaded = 0
    total = 1

    def __init__(self, url, opt):
        self.url = url
        self.opt = opt

    def update(self, data):
        if data["status"] == "finished":
            self.downloaded = self.total
            self.finished = True
        elif data["status"] == "downloading":
            if "total_bytes" in data:
                self.total = data["total_bytes"]
            if "downloaded_bytes" in data:
                self.downloaded = data["downloaded_bytes"]


progress = Progress("", {})
dl_q = Queue()
done = False
dl_thread = Thread(target=dl_worker)
dl_thread.start()

print("Started download thread")

app_vars = ChainMap(os.environ, app_defaults)

app.run(host=app_vars['YDL_SERVER_HOST'],
        port=app_vars['YDL_SERVER_PORT'], debug=True)
done = True
dl_thread.join()
