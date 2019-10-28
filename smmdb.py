import pathlib
from datetime import datetime
from dataclasses import dataclass
from enum import Enum
from zipfile import ZipFile
import requests
import os
import io
import subprocess
import zlib
import struct
import jsons
from threading import Thread
import queue
import logging

logger = logging.getLogger(__name__)


class Difficulty(Enum):
    Easy = 0
    Normal = 1
    Expert = 2
    SuperExpert = 3


class GameStyle(Enum):
    SMB = 0
    SMB3 = 1
    SMW = 2
    NSMBU = 3


class Theme(Enum):
    Ground = 0
    Underground = 1
    Castle = 2
    Airship = 3
    Underwater = 4
    Ghosthouse = 5


class ScrollSpeed(Enum):
    Disabled = 0
    Slow = 1
    Medium = 2
    Fast = 3


@dataclass
class Course:
    title: str
    maker: str
    gameStyle: GameStyle
    courseTheme: Theme
    courseThemeSub: Theme
    time: int
    autoScroll: ScrollSpeed
    autoScrollSub: ScrollSpeed
    width: int
    widthSub: int
    owner: str
    nintendoid: str
    videoid: str
    difficulty: Difficulty
    lastmodified: datetime
    uploaded: datetime
    description: str
    stars: int
    starred: bool
    uploader: str
    id: str


"""
typedef unsigned char uint8;
typedef unsigned short uint16;
typedef unsigned int uint32;

/*
The level format is 4 chunks that start with ASH0:
- chunk1: thumbnail0.tnl (compressed)
- chunk2: course_data.cdt (compressed)
- chunk3: course_data_sub.cdt (compressed)
- chunk4: thumbnail1.tnl (compressed)
See https://github.com/PretendoNetwork/ASH0 for decompression code.
See https://github.com/Treeki/MarioUnmaker/blob/master/FormatNotes.md for decompressed level format.
*/

BigEndian();
struct MetaBinarySmm {
    uint32 unk1; // observed values: 1, 2, 3
    uint32 chunk2_theme; // Course theme (0 = overworld, 1 = underground, 2 = castle, 3 = airship, 4 = water, 5 = ghost house)

    uint32 chunk2_size; // course_data.cdt (compressed)
    uint32 chunk3_size; // course_data_sub.cdt (compressed)
    uint32 chunk1_size; // thumbnail0.tnl (compressed)
    uint32 chunk4_size; // thumbnail1.tnl (compressed)

    uint32 unk3; // observed values: 1, 2, 3

    uint32 chunk2_crc32; // course_data.cdt (compressed)
    uint32 chunk3_crc32; // course_data_sub.cdt (compressed)
    uint32 chunk1_crc32; // thumbnail0.tnl (compressed)
    uint32 chunk4_crc32; // thumbnail1.tnl (compressed)
};
"""


@dataclass
class MetaBinary:
    unk1: int
    chunk2_theme: int

    chunk2_size: int
    chunk3_size: int
    chunk1_size: int
    chunk4_size: int

    unk3: int

    chunk2_crc32: int
    chunk3_crc32: int
    chunk1_crc32: int
    chunk4_crc32: int

    def __init__(self, theme, chunk1, chunk2, chunk3, chunk4):
        self.unk1 = 1
        self.unk3 = 1
        self.chunk2_theme = theme
        self.chunk1_size = len(chunk1)
        self.chunk1_crc32 = zlib.crc32(chunk1)
        self.chunk2_size = len(chunk2)
        self.chunk2_crc32 = zlib.crc32(chunk2)
        self.chunk3_size = len(chunk3)
        self.chunk3_crc32 = zlib.crc32(chunk3)
        self.chunk4_size = len(chunk4)
        self.chunk4_crc32 = zlib.crc32(chunk4)

    def course_size(self):
        return self.chunk1_size + self.chunk2_size + self.chunk3_size + self.chunk4_size

    def to_bytes(self):
        return struct.pack(">IIIIIIIIIII",
                           self.unk1,
                           self.chunk2_theme,
                           self.chunk2_size,
                           self.chunk3_size,
                           self.chunk1_size,
                           self.chunk4_size,
                           self.unk3,
                           self.chunk2_crc32,
                           self.chunk3_crc32,
                           self.chunk1_crc32,
                           self.chunk4_crc32)


def read_file(file):
    return pathlib.Path(file).read_bytes()


def ash_compress(file):
    fnull = open(os.devnull, 'w')
    subprocess.call(['ashcompress.exe', file], stdout=fnull, stderr=subprocess.STDOUT)
    ash_file = file + '.ash'
    subprocess.call(['ASH.exe', ash_file], stdout=fnull, stderr=subprocess.STDOUT)
    arc_file = ash_file + '.arc'
    original = read_file(file)
    decompressed = read_file(arc_file)
    if original != decompressed:
        raise Exception('ASH compression failure for {}'.format(file))
    return read_file(ash_file)


def enum_dir(base_dir, *, recursive):
    for entry in os.scandir(base_dir):
        if entry.is_file():
            yield os.path.join(base_dir, entry.name)
        elif recursive:
            yield from enum_dir(entry.path, recursive=True)


def mkdir(d):
    # noinspection PyBroadException
    try:
        os.mkdir(d)
    except:
        pass


def create_smmdb():
    mkdir('www/smmdb')
    mkdir('www/smmdb/0')
    mkdir('www/smmdb/1')
    mkdir('www/smmdb/2')
    mkdir('www/smmdb/3')
    mkdir('www/smmdb/tmp')


def get_next_index():
    max_index = 10000000000 - 1
    for file in enum_dir('www/smmdb', recursive=True):
        filename = os.path.basename(file)
        if filename.endswith('-00001'):
            index = int(filename[:filename.index('-00001')])
            max_index = max(max_index, index)
    return max_index + 1


def get_course_files(basedir, *, recursive=False):
    for file in enum_dir(basedir, recursive=recursive):
        if file.endswith('-00001'):
            yield file


def fetch_courses(difficulty, total_required):
    create_smmdb()
    total_played = 0
    total_unplayed = 0
    basedir = 'www/smmdb/{}'.format(difficulty.value)
    for course_file in get_course_files(basedir):
        if os.path.isfile(course_file + '.played'):
            total_played += 1
        else:
            total_unplayed += 1
    if total_unplayed >= total_required:
        logger.info('[smmdb] nothing to do for {}'.format(difficulty))
        return
    total_fetch = total_required - total_unplayed
    logger.info('[smmdb] fetching {} {} courses'.format(total_fetch, difficulty))
    fetched = 0
    index = get_next_index()
    while True:
        get_params = {
            'limit': 100,
            'random': 1,
            'difficultyfrom': difficulty.value,
            'difficultyto': difficulty.value
        }
        r_get = requests.get('https://smmdb.ddns.net/api/getcourses', get_params)
        if r_get.status_code != 200:
            logger.info('[smmdb] getcourses error {}'.format(r_get.status_code))
        else:
            courses = r_get.json()
            for course in courses:
                course_id = course['id']
                if os.path.isfile(basedir + course_id):
                    logger.info('[smmdb] skipping {}'.format(course_id))
                    continue
                course_theme = course['courseTheme']
                download_params = {
                    'id': course_id,
                    'type': 'zip'
                }
                r_zip = requests.get('https://smmdb.ddns.net/api/downloadcourse', download_params)
                if r_zip.status_code != 200:
                    logger.info('[smmdb] downloadcourse error {} (id: {})'.format(r_zip.status_code, course_id))
                else:
                    logger.info('[smmdb] downloaded {} (index: {})'.format(course_id, index))
                    with ZipFile(io.BytesIO(r_zip.content), 'r') as zf:
                        zf.extractall('www/smmdb/tmp')

                    try:
                        chunk1 = ash_compress('www/smmdb/tmp/course000/thumbnail0.tnl')
                        chunk2 = ash_compress('www/smmdb/tmp/course000/course_data.cdt')
                        chunk3 = ash_compress('www/smmdb/tmp/course000/course_data_sub.cdt')
                        chunk4 = ash_compress('www/smmdb/tmp/course000/thumbnail1.tnl')
                    except Exception as x:
                        logger.info('[smmdb] ' + str(x))
                        continue

                    meta_binary = MetaBinary(course_theme, chunk1, chunk2, chunk3, chunk4)
                    course['meta_binary'] = meta_binary.to_bytes()
                    course['index'] = index
                    course['size'] = meta_binary.course_size()
                    basename = basedir + '/%011u-00001' % index
                    with open(basename + '.json', 'wb') as mf:
                        mf.write(jsons.dumpb(course, encoding='utf8'))
                    with open(basename, 'wb') as cf:
                        cf.write(chunk1)
                        cf.write(chunk2)
                        cf.write(chunk3)
                        cf.write(chunk4)
                    with open(basedir + course_id, 'w') as vf:
                        vf.write('{}'.format(index))
                    # next course index
                    index += 1
                    fetched += 1
                    if fetched >= total_fetch:
                        return


# Based on: https://medium.com/@shashwat_ds/a-tiny-multi-threaded-job-queue-in-30-lines-of-python-a344c3f3f7f0
class TaskQueue(queue.Queue):
    def __init__(self, num_workers=1):
        queue.Queue.__init__(self)
        self.num_workers = num_workers
        self.start_workers()

    def add_task(self, task, *args, **kwargs):
        args = args or ()
        kwargs = kwargs or {}
        self.put((task, args, kwargs))

    def start_workers(self):
        for i in range(self.num_workers):
            t = Thread(target=self.worker)
            t.daemon = True
            t.start()

    def worker(self):
        while True:
            item, args, kwargs = self.get()
            item(*args, **kwargs)
            self.task_done()


class SmmdbQueue:
    def __init__(self):
        self._task_queue = TaskQueue()

    def fetch_async(self):
        self._task_queue.add_task(fetch_all_difficulties)

    def wait(self):
        self._task_queue.join()


def fetch_all_difficulties():
    fetch_courses(Difficulty.Easy, 400)
    fetch_courses(Difficulty.Normal, 400)
    fetch_courses(Difficulty.Expert, 400)
    fetch_courses(Difficulty.SuperExpert, 400)


def main():
    fetch_all_difficulties()


if __name__ == "__main__":
    main()
