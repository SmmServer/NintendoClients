import pathlib
from datetime import datetime
from dataclasses import dataclass
from enum import Enum
from zipfile import ZipFile
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import os
import io
import zlib
import struct
import jsons
from threading import Thread, Lock
import queue
import logging
import time
import configparser
import random
import sys
import shutil
import re
import heapq
from collections import Counter
import json
import base64
import traceback

sys.stderr = sys.stdout

logger = logging.getLogger(__name__)

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))

if getattr(sys, 'frozen', False):
    ROOT_DIR = os.path.dirname(sys.executable)
else:
    ROOT_DIR = os.path.dirname(CURRENT_DIR)

SETTINGS_INI_PATH = os.path.join(ROOT_DIR, 'Configs', 'settings.ini')
BASE_SMMDB_DIR = os.path.join(CURRENT_DIR, 'www', 'smmdb')
BASE_CW_DIR = os.path.join(CURRENT_DIR, 'www', 'courseworld')
TMP_DIR = os.path.join(CURRENT_DIR, 'www', 'tmp')

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

def get_settings():
    config = configparser.ConfigParser()
    try:
        if os.path.exists(SETTINGS_INI_PATH):
            config.read(SETTINGS_INI_PATH)
    except: pass
    source = config.get('General', 'CourseSource', fallback='SMMDB')
    return source

def is_online():
    try:
        requests.get("http://www.google.com", timeout=3)
        return True
    except requests.exceptions.RequestException:
        return False

def get_session():
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Connection': 'keep-alive',
    })
    retry_strategy = Retry(
        total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["HEAD", "GET", "OPTIONS"]
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session

class BitStream:
    def __init__(self, data=b''):
        self.data = bytearray(data)
        self.pos = 0; self.bits = 0; self.bit_count = 0; self.word = 0
    def read_bit(self):
        if self.bit_count == 0:
            if self.pos >= len(self.data): return 0
            chunk = self.data[self.pos:self.pos+4]
            if len(chunk) < 4: chunk += b'\x00' * (4 - len(chunk))
            self.word = struct.unpack('>I', chunk)[0]
            self.pos += 4
            self.bit_count = 32
        bit = (self.word >> 31) & 1
        self.word = (self.word << 1) & 0xFFFFFFFF
        self.bit_count -= 1
        return bit
    def read_bits(self, n):
        val = 0
        for _ in range(n): val = (val << 1) | self.read_bit()
        return val
    def write_bit(self, bit):
        self.bits = (self.bits << 1) | (bit & 1)
        self.bit_count += 1
        if self.bit_count == 32:
            self.data.extend(struct.pack('>I', self.bits)); self.bits = 0; self.bit_count = 0
    def write_bits(self, val, n):
        for i in range(n - 1, -1, -1): self.write_bit((val >> i) & 1)
    def flush(self):
        if self.bit_count > 0: self.data.extend(struct.pack('>I', self.bits << (32 - self.bit_count)))
        return bytes(self.data)

def ash0_decompress(data):
    if len(data) < 12 or data[:4] != b'ASH0': return b''
    decomp_size = int.from_bytes(data[4:8], 'big') & 0xFFFFFF
    sym_offset = int.from_bytes(data[8:12], 'big')
    if sym_offset >= len(data): return b''
    out = bytearray(decomp_size); out_pos = 0
    dist_reader = BitStream(data[sym_offset:]); sym_reader = BitStream(data[12:])
    def read_tree(reader, width, max_nodes):
        nodes = [(0, 0)] * (max_nodes * 2); root = 0; node_count = 1; stack = []
        while True:
            if reader.read_bit():
                nodes[root] = (node_count, node_count + 1); stack.append(node_count + 1)
                root = node_count; node_count += 2
            else:
                nodes[root] = (reader.read_bits(width), None)
                if not stack: break
                root = stack.pop()
        return nodes
    try:
        sym_tree = read_tree(sym_reader, 9, 1 << 9)
        dist_tree = read_tree(dist_reader, 11, 1 << 11)
    except: return b''
    def get_huffman_code(reader, tree):
        node = tree[0]
        while node[1] is not None: node = tree[node[reader.read_bit()]]
        return node[0]
    while out_pos < decomp_size:
        try:
            sym = get_huffman_code(sym_reader, sym_tree)
            if sym < 0x100: out[out_pos] = sym; out_pos += 1
            else:
                length = sym - 0x100 + 3; dist = get_huffman_code(dist_reader, dist_tree) + 1; copy_pos = out_pos - dist
                for _ in range(length):
                    if out_pos >= decomp_size: break
                    out[out_pos] = out[copy_pos]; out_pos += 1; copy_pos += 1
        except: break
    return bytes(out)

class Node:
    def __init__(self, freq, symbol=None, left=None, right=None):
        self.freq = freq; self.symbol = symbol; self.left = left; self.right = right
    def __lt__(self, other): return self.freq < other.freq
def build_huffman_tree(freqs):
    heap = [Node(freq, sym) for sym, freq in freqs.items()]; heapq.heapify(heap)
    while len(heap) > 1: l = heapq.heappop(heap); r = heapq.heappop(heap); heapq.heappush(heap, Node(l.freq + r.freq, left=l, right=r))
    return heap[0] if heap else None
def get_codes(node, prefix='', codebook=None):
    if codebook is None: codebook = {}
    if node.symbol is not None: codebook[node.symbol] = prefix
    else: get_codes(node.left, prefix + '0', codebook); get_codes(node.right, prefix + '1', codebook)
    return codebook
def write_tree(node, stream, width):
    if node.symbol is not None: stream.write_bit(0); stream.write_bits(node.symbol, width)
    else: stream.write_bit(1); write_tree(node.left, stream, width); write_tree(node.right, stream, width)
def ash0_compress(raw_data):
    freqs = Counter(raw_data); [freqs.update({i: 1}) for i in range(256) if i not in freqs]
    sym_tree = build_huffman_tree(freqs); sym_codes = get_codes(sym_tree)
    dist_tree = Node(1, symbol=0); sym_stream = BitStream(); dist_stream = BitStream()
    write_tree(sym_tree, sym_stream, 9); write_tree(dist_tree, dist_stream, 11)
    for byte in raw_data: [sym_stream.write_bit(int(b)) for b in sym_codes[byte]]
    sym_bytes = sym_stream.flush(); dist_bytes = dist_stream.flush()
    header = bytearray(b'ASH0'); header.extend(struct.pack('>I', len(raw_data))); header.extend(struct.pack('>I', 12 + len(sym_bytes)))
    return bytes(header) + sym_bytes + dist_bytes
def ash_compress_python(d): return ash0_compress(d)

class Difficulty(Enum): Easy = 0; Normal = 1; Expert = 2; SuperExpert = 3
@dataclass
class MetaBinary:
    def __init__(self, theme, chunk1, chunk2, chunk3, chunk4):
        self.unk1 = 1; self.unk3 = 1; self.chunk2_theme = theme
        self.chunk1_size = len(chunk1); self.chunk1_crc32 = zlib.crc32(chunk1)
        self.chunk2_size = len(chunk2); self.chunk2_crc32 = zlib.crc32(chunk2)
        self.chunk3_size = len(chunk3); self.chunk3_crc32 = zlib.crc32(chunk3)
        self.chunk4_size = len(chunk4); self.chunk4_crc32 = zlib.crc32(chunk4)
    def to_bytes(self):
        return struct.pack(">IIIIIIIIIII", self.unk1, self.chunk2_theme, self.chunk2_size,
                           self.chunk3_size, self.chunk1_size, self.chunk4_size, self.unk3,
                           self.chunk2_crc32, self.chunk3_crc32, self.chunk1_crc32, self.chunk4_crc32)

def mkdir(d): os.makedirs(d, exist_ok=True)
def create_dirs():
    for i in range(4):
        mkdir(os.path.join(BASE_SMMDB_DIR, str(i)))
        mkdir(os.path.join(BASE_CW_DIR, str(i)))
    mkdir(TMP_DIR)

def get_next_index():
    max_index = 9999999999
    for base in [BASE_SMMDB_DIR, BASE_CW_DIR]:
        if not os.path.exists(base): continue
        for root, _, files in os.walk(base):
            for file in files:
                if file.endswith('-00001'):
                    try: max_index = max(max_index, int(os.path.basename(file).split('-')[0]))
                    except: continue
    return max_index + 1

class CacheManager:
    def __init__(self, progress_queue=None, log_queue=None):
        self.progress_queue = progress_queue
        self.log_queue = log_queue
        self.current_page_courses = []
        self.is_bootstrapping = False
        self.current_source_type = 'SMMDB'
        self.session = get_session()
        create_dirs()

    def log(self, message):
        msg_str = f"[CacheManager] {message}"
        if self.log_queue: self.log_queue.put(("Debug", msg_str))
        print(msg_str, flush=True)

    def get_unplayed_count(self, difficulty):
        source = get_settings()
        target_dir = BASE_CW_DIR if source == 'CourseWorld' else BASE_SMMDB_DIR
        count = 0
        path = os.path.join(target_dir, str(difficulty.value))
        if os.path.exists(path):
            count = sum(1 for f in os.listdir(path) if f.endswith('-00001') and not os.path.exists(os.path.join(path, f + '.played')))
        return count

    def get_total_count(self, difficulty):
        source = get_settings()
        target_dir = BASE_CW_DIR if source == 'CourseWorld' else BASE_SMMDB_DIR
        count = 0
        path = os.path.join(target_dir, str(difficulty.value))
        if os.path.exists(path):
            count = sum(1 for f in os.listdir(path) if f.endswith('-00001'))
        return count

    def start_worker(self):
        Thread(target=self.worker_loop, daemon=True).start()

    def worker_loop(self):
        self.current_source_type = get_settings()
        self.log(f"Active source: {self.current_source_type}")

        unplayed_count = self.get_unplayed_count(Difficulty.Normal)
        self.log(f"Unplayed courses found: {unplayed_count}")

        if unplayed_count == 0:
            self.log("Cache empty for this source.")
            if is_online():
                self.log("Starting Bootstrap...")
                self.is_bootstrapping = True
                if self.progress_queue: self.progress_queue.put(("Bootstrapping Cache", 0, 20))

                if self.fetch_new_page():
                    self.process_batch(20, "Bootstrapping Cache")
                else:
                    self.log("Bootstrap failed.")

                if self.progress_queue: self.progress_queue.put(("Bootstrapping Cache", 20, 20))
                self.is_bootstrapping = False
                self.log("Bootstrap finished.")
            else:
                self.log("Offline. Bootstrap skipped.")

        self.log("Entering main loop.")

        while True:
            try:
                new_source = get_settings()
                if new_source != self.current_source_type:
                    self.log(f"Source switched to {new_source}. Clearing page cache.")
                    self.current_source_type = new_source
                    self.current_page_courses = []

                unplayed = self.get_unplayed_count(Difficulty.Normal)
                target = 80

                if unplayed < target:
                    download_possible = False
                    if is_online():
                        if not self.current_page_courses:
                            if self.fetch_new_page():
                                download_possible = True
                        else:
                            download_possible = True

                    if download_possible:
                         self.process_batch(5, "Background Caching")
                    else:
                        total_courses = self.get_total_count(Difficulty.Normal)
                        if total_courses > 0:
                            time.sleep(60)
                            continue

            except Exception as e:
                self.log(f"Loop error: {e}")

            time.sleep(5)

    def fetch_new_page(self):
        current_source = self.current_source_type
        try:
            if current_source == 'CourseWorld':
                page_num = random.randint(0, 88024)
                url = f"https://gitlab.com/lsouzaperfeito/smmserver/-/raw/main/page_{page_num}.json"
                self.log(f"Requesting index: {url}")

                r = self.session.get(url, timeout=20)
                r.raise_for_status()

                self.current_page_courses = r.json()
                self.log(f"Fetched {len(self.current_page_courses)} courses from CourseWorld.")
                return True
            else:
                url = 'https://smmdb.net/api/getcourses'
                self.log(f"Querying SMMDB API...")

                temp_headers = self.session.headers.copy()
                temp_headers.pop('User-Agent', None)
                r = self.session.get(url, params={'limit': 100, 'random': 1}, timeout=20, headers=temp_headers)
                r.raise_for_status()

                self.current_page_courses = r.json()
                self.log(f"Fetched {len(self.current_page_courses)} courses from SMMDB.")
                return True

        except requests.exceptions.RequestException as e:
            self.log(f"Server Connection Error: {e}")
            self.current_page_courses = []
            return False
        except Exception as e:
            self.log(f"Error: {e}\n{traceback.format_exc()}")
            self.current_page_courses = []
            return False

    def process_batch(self, count, p_type):
        fetched = 0
        while fetched < count and self.current_page_courses:
            course = self.current_page_courses.pop(0)
            if self.download_and_process(course):
                fetched += 1
                if self.progress_queue and self.is_bootstrapping:
                     self.progress_queue.put((p_type, fetched, count))

    def download_and_process(self, course):
        if 'id' not in course: return False

        course_id = course['id']
        index = get_next_index()
        dest_folder = BASE_CW_DIR if self.current_source_type == 'CourseWorld' else BASE_SMMDB_DIR
        basedir = os.path.join(dest_folder, str(Difficulty.Normal.value))
        mkdir(basedir)

        try:
            if self.current_source_type == 'CourseWorld':
                archive_url = f"https://web.archive.org/web/0id_/{course_id}"

                data = None
                download_success = False
                try:
                    r = self.session.get(archive_url, timeout=60, stream=True)
                    if r.status_code == 200:
                        data = r.content
                        download_success = True
                    else:
                        self.log(f"HTTP {r.status_code}: {archive_url}")
                except Exception as ex:
                    self.log(f"Error: {ex}")

                time.sleep(1)

                if not download_success or not data: return False
                if data[:15].strip().lower().startswith(b'<!doctype'): return False

                separator = b'ASH0'
                starts = [m.start() for m in re.finditer(separator, data)]
                if len(starts) != 4: return False

                try:
                    chunks = [data[starts[i]:starts[i+1]] for i in range(3)] + [data[starts[3]:]]
                    for part in chunks:
                        if not ash0_decompress(part): raise ValueError("Corrupt")
                except: return False

                basename = os.path.join(basedir, f'{index:011d}-00001')
                with open(basename + '.json', 'w') as f: json.dump(course, f)
                with open(basename, 'wb') as f: f.write(data)

                self.log(f"SAVED: {index}")
                return True

            else:
                download_url = f'https://smmdb.net/api/downloadcourse?id={course_id}&type=zip'
                r = self.session.get(download_url, timeout=60)
                r.raise_for_status()

                with ZipFile(io.BytesIO(r.content), 'r') as zf:
                    c1 = ash_compress_python(zf.read('course000/thumbnail0.tnl'))
                    c2 = ash_compress_python(zf.read('course000/course_data.cdt'))
                    c3 = ash_compress_python(zf.read('course000/course_data_sub.cdt'))
                    c4 = ash_compress_python(zf.read('course000/thumbnail1.tnl'))
                meta = MetaBinary(course['courseTheme'], c1, c2, c3, c4)
                course['meta_binary_b64'] = base64.b64encode(meta.to_bytes()).decode('ascii')
                basename = os.path.join(basedir, f'{index:011d}-00001')
                with open(basename + '.json', 'w') as f: json.dump(course, f)
                with open(basename, 'wb') as f: f.write(c1 + c2 + c3 + c4)

                self.log(f"SAVED: {index}")
                return True
        except Exception as e:
            self.log(f"Failed {course_id}: {e}")
            return False

def start_cache_worker(p_queue, l_queue):
    mgr = CacheManager(p_queue, l_queue)
    mgr.start_worker()