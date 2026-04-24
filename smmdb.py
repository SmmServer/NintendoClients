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
STORAGE_DIR = os.getenv("SMM_STORAGE_DIR")

if STORAGE_DIR:
    ROOT_DIR = STORAGE_DIR
    WWW_ROOT = os.path.join(STORAGE_DIR, 'NintendoClients', 'www')
else:
    if getattr(sys, 'frozen', False):
        ROOT_DIR = os.path.dirname(sys.executable)
    else:
        ROOT_DIR = os.path.dirname(CURRENT_DIR)
    WWW_ROOT = os.path.join(CURRENT_DIR, 'www')

SETTINGS_INI_PATH = os.path.join(ROOT_DIR, 'Configs', 'settings.ini')
BASE_SMMDB_DIR = os.path.join(WWW_ROOT, 'smmdb')
BASE_CW_DIR = os.path.join(WWW_ROOT, 'courseworld')
LISTS_DIR = os.path.join(WWW_ROOT, 'lists') 
TMP_DIR = os.path.join(WWW_ROOT, 'tmp')
SYSTEM_ID = 10000000200
BOOTSTRAP_LIMIT = 20
MAINTENANCE_LIMIT = 40

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
    mkdir(LISTS_DIR)

def get_next_index():
    max_index = 0
    # Scan for the highest existing index
    for base in [BASE_SMMDB_DIR, BASE_CW_DIR]:
        if not os.path.exists(base): continue
        for root, _, files in os.walk(base):
            for file in files:
                if file.endswith('-00001'):
                    try: 
                        idx = int(os.path.basename(file).split('-')[0])
                        # Ignore the system ID when calculating max index
                        if idx == SYSTEM_ID:
                            continue
                        max_index = max(max_index, idx)
                    except: continue
    
    # Calculate next index
    next_idx = max_index + 1
    
    # If the natural next index happens to be the reserved system ID, skip it
    if next_idx == SYSTEM_ID:
        next_idx += 1
        
    return next_idx

class CacheManager:
    def __init__(self, progress_queue=None, log_queue=None):
        self.progress_queue = progress_queue
        self.log_queue = log_queue
        self.session = get_session()
        self.current_source_type = 'SMMDB'
        self.downloaded_batch = []
        self.is_bootstrapping = False
        create_dirs()

    def log(self, message):
        msg_str = f"[CacheManager] {message}"
        if self.log_queue:
            self.log_queue.put(("Debug", msg_str))
        else:
            print(msg_str, flush=True)

    def log_status(self, message):
        """Dedicated log function for status updates to bypass generic prefixing"""
        msg_str = f"Status: {message}"
        if self.log_queue:
            self.log_queue.put(("CacheStatus", msg_str))
        else:
            print(f"[CacheStatus] {msg_str}", flush=True)

    def get_random_count(self, difficulty=None):
        source = get_settings()
        
        exclude_ids = set()
        if source != 'CourseWorld':
            for list_file in ["new_arrivals.json", "star_ranking.json"]:
                path = os.path.join(LISTS_DIR, list_file)
                if os.path.exists(path):
                    try:
                        with open(path, 'r') as f:
                            exclude_ids.update(json.load(f))
                    except: pass

        target_dir = BASE_CW_DIR if source == 'CourseWorld' else BASE_SMMDB_DIR
        
        # If difficulty is provided, check only that folder.
        # If None, check all folders.
        diffs_to_check = [difficulty.value] if difficulty else [0, 1, 2, 3]
        
        count = 0
        for d in diffs_to_check:
            path = os.path.join(target_dir, str(d))
            if os.path.exists(path):
                for f in os.listdir(path):
                    if f.endswith('-00001') and not os.path.exists(os.path.join(path, f + '.played')):
                        try:
                            idx_str = f.split('-')[0]
                            idx = int(idx_str)
                            if idx not in exclude_ids:
                                count += 1
                        except: pass
        return count

    def get_list_count(self, list_name):
        path = os.path.join(LISTS_DIR, list_name)
        if not os.path.exists(path): return 0
        try:
            with open(path, 'r') as f:
                data = json.load(f)
                return len(data)
        except: return 0

    def start_worker(self):
        Thread(target=self.worker_loop, daemon=True).start()
    
    def perform_status_update(self):
        if self.current_source_type == 'CourseWorld':
            count = self.get_random_count(Difficulty.Normal)
            self.log_status(f"Status: {count} unplayed courses available.")
        else:
            # Check all difficulties separately
            e = self.get_random_count(Difficulty.Easy)
            n = self.get_random_count(Difficulty.Normal)
            ex = self.get_random_count(Difficulty.Expert)
            sx = self.get_random_count(Difficulty.SuperExpert)
            
            upl = self.get_list_count("new_arrivals.json")
            sta = self.get_list_count("star_ranking.json")
            
            self.log_status(f"Status: [E:{e} N:{n} X:{ex} S:{sx}], Uploaded={upl}, Stars={sta}")

    def are_pools_ready(self):
        if self.current_source_type == 'CourseWorld':
            return self.get_random_count(Difficulty.Normal) >= BOOTSTRAP_LIMIT
        else:
            # Check if EACH difficulty has enough courses for bootstrap
            e = self.get_random_count(Difficulty.Easy) >= BOOTSTRAP_LIMIT
            n = self.get_random_count(Difficulty.Normal) >= BOOTSTRAP_LIMIT
            ex = self.get_random_count(Difficulty.Expert) >= BOOTSTRAP_LIMIT
            sx = self.get_random_count(Difficulty.SuperExpert) >= BOOTSTRAP_LIMIT
            
            upl = self.get_list_count("new_arrivals.json") >= BOOTSTRAP_LIMIT
            sta = self.get_list_count("star_ranking.json") >= BOOTSTRAP_LIMIT
            
            return e and n and ex and sx and upl and sta

    def ensure_system_courses(self):
        system_id = SYSTEM_ID
        
        while True:
            missing = False
            for i in range(4):
                path_req = os.path.join(BASE_SMMDB_DIR, str(i), f"{system_id}-0000{i}")
                if not os.path.exists(path_req):
                    missing = True
                    break
            
            if not missing:
                return

            template_binary = None
            template_json = None
            found_local = False

            for diff in range(4):
                search_path = os.path.join(BASE_SMMDB_DIR, str(diff))
                if os.path.exists(search_path):
                    for f in os.listdir(search_path):
                        if f.endswith('-00001') and not f.startswith(str(system_id)):
                            full_path = os.path.join(search_path, f)
                            json_path = full_path + '.json'
                            if os.path.exists(json_path):
                                try:
                                    with open(full_path, 'rb') as bf: template_binary = bf.read()
                                    with open(json_path, 'r') as jf: template_json = json.load(jf)
                                    found_local = True
                                    break
                                except Exception as e:
                                    pass 
                    if found_local: break

            if found_local and template_binary and template_json:
                try:
                    template_json['id'] = str(system_id)

                    for diff in range(4):
                        folder = os.path.join(BASE_SMMDB_DIR, str(diff))
                        mkdir(folder)
                        
                        fname_req = f"{system_id}-0000{diff}"
                        fpath_req = os.path.join(folder, fname_req)
                        
                        fname_std = f"{system_id}-00001"
                        fpath_std = os.path.join(folder, fname_std)
                        
                        if not os.path.exists(fpath_req):
                            with open(fpath_req + '.json', 'w') as f: json.dump(template_json, f)
                            with open(fpath_req, 'wb') as f: 
                                f.write(template_binary)
                                f.flush()
                                os.fsync(f.fileno())
                        
                        if diff != 1 and not os.path.exists(fpath_std):
                            with open(fpath_std + '.json', 'w') as f: json.dump(template_json, f)
                            with open(fpath_std, 'wb') as f: 
                                f.write(template_binary)
                                f.flush()
                                os.fsync(f.fileno())
                    
                    return

                except Exception as e:
                    pass
            else:
                time.sleep(5)


    def worker_loop(self):
        self.current_source_type = get_settings()
        self.log(f"Active source: {self.current_source_type}")

        if is_online():
            # Start the system check thread.
            Thread(target=self.ensure_system_courses, daemon=True).start()

            # Bootstrapping logic (20 per category)
            if not self.are_pools_ready():
                self.is_bootstrapping = True
                
                # Blocks UI
                if self.progress_queue:
                    self.progress_queue.put(("BOOT_START", None))
                    
                if self.current_source_type == 'CourseWorld':
                     self.ensure_pool("CourseWorld", "random", BOOTSTRAP_LIMIT) 
                else:
                     # Bootstrap each difficulty + lists separately
                     self.ensure_pool("Easy", "random", BOOTSTRAP_LIMIT, difficulty=Difficulty.Easy)
                     self.ensure_pool("Normal", "random", BOOTSTRAP_LIMIT, difficulty=Difficulty.Normal)
                     self.ensure_pool("Expert", "random", BOOTSTRAP_LIMIT, difficulty=Difficulty.Expert)
                     self.ensure_pool("Super Expert", "random", BOOTSTRAP_LIMIT, difficulty=Difficulty.SuperExpert)
                     self.ensure_pool("New Arrivals", "uploaded", BOOTSTRAP_LIMIT)
                     self.ensure_pool("Star Ranking", "stars", BOOTSTRAP_LIMIT)
                
                self.is_bootstrapping = False
            
            # Release UI
            if self.progress_queue:
                self.progress_queue.put(("BOOT_END", None))

            self.perform_status_update()
        
        last_status_log = time.time()

        while True:
            try:
                new_source = get_settings()
                if new_source != self.current_source_type:
                    self.log(f"Source switched to {new_source}. Clearing page cache.")
                    self.current_source_type = new_source
                    self.current_page_courses = []

                if is_online():
                    # Check timer and update status
                    if time.time() - last_status_log > 5:
                        self.perform_status_update()
                        last_status_log = time.time()

                    if self.current_source_type == 'CourseWorld':
                         self.maintain_pool_logic("CourseWorld", "random", 80)
                    else:
                         # Maintenance
                         self.maintain_pool_logic("Easy", "random", MAINTENANCE_LIMIT, difficulty=Difficulty.Easy)
                         self.maintain_pool_logic("Normal", "random", MAINTENANCE_LIMIT, difficulty=Difficulty.Normal)
                         self.maintain_pool_logic("Expert", "random", MAINTENANCE_LIMIT, difficulty=Difficulty.Expert)
                         self.maintain_pool_logic("Super Expert", "random", MAINTENANCE_LIMIT, difficulty=Difficulty.SuperExpert)
                         self.maintain_pool_logic("New Arrivals", "uploaded", MAINTENANCE_LIMIT)
                         self.maintain_pool_logic("Star Ranking", "stars", MAINTENANCE_LIMIT)

                else:
                    self.log("Offline. Sleeping...")

            except Exception as e:
                self.log(f"Loop error: {e}")

            time.sleep(60)

    def ensure_pool(self, name, order_mode, target, difficulty=None):
        count = 0
        if order_mode == "random":
             count = self.get_random_count(difficulty)
        else:
            filename = "new_arrivals.json" if order_mode == "uploaded" else "star_ranking.json"
            count = self.get_list_count(filename)
        
        if count < target:
            if self.fetch_new_page(order=order_mode, difficulty=difficulty):
                self.process_batch(target - count, f"Bootstrapping {name} cache")

    def maintain_pool_logic(self, name, order_mode, target, difficulty=None):
        count = 0
        if order_mode == "random":
             count = self.get_random_count(difficulty)
        else:
            filename = "new_arrivals.json" if order_mode == "uploaded" else "star_ranking.json"
            count = self.get_list_count(filename)

        if count < target:
            if self.fetch_new_page(order=order_mode, difficulty=difficulty):
                self.process_batch(target - count, f"{name}")

    def fetch_new_page(self, order="random", difficulty=None):
        current_source = self.current_source_type
        self.fetched_ids_in_batch = []
        self.current_order_mode = order

        try:
            if current_source == 'CourseWorld':
                page_num = random.randint(0, 88024)
                url = f"https://gitlab.com/lsouzaperfeito/smmserver/-/raw/main/page_{page_num}.json"

                r = self.session.get(url, timeout=20)
                r.raise_for_status()

                self.current_page_courses = r.json()
                return True
            else:
                url = 'https://smmdb.net/api/getcourses'
                params = {'limit': 100}
                
                if order == "uploaded":
                    params['order'] = "uploaded"
                elif order == "stars":
                    params['order'] = "stars"
                elif order == "random":
                    if difficulty is not None:
                        params['difficultyfrom'] = difficulty.value
                        params['difficultyto'] = difficulty.value

                temp_headers = self.session.headers.copy()
                temp_headers.pop('User-Agent', None)
                r = self.session.get(url, params=params, timeout=20, headers=temp_headers)
                r.raise_for_status()

                self.current_page_courses = r.json()
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
            saved_id = self.download_and_process(course)
            if saved_id:
                fetched += 1
                self.fetched_ids_in_batch.append(saved_id)
                
                if self.progress_queue and self.is_bootstrapping:
                     self.progress_queue.put(("PROGRESS", (p_type, fetched, count)))
        
        # Even if we sort by difficulty into folders, we still add "uploaded" and "stars" to the lists
        # so they appear in the browser mixed.
        if self.current_source_type == 'SMMDB' and self.current_order_mode in ["uploaded", "stars"]:
            self.update_list_file(self.current_order_mode)

    def update_list_file(self, mode):
        filename = "new_arrivals.json" if mode == "uploaded" else "star_ranking.json"
        path = os.path.join(LISTS_DIR, filename)
        
        existing = []
        if os.path.exists(path):
            try:
                with open(path, 'r') as f: existing = json.load(f)
            except: pass
        
        new_list = []
        seen = set()
        
        for i in self.fetched_ids_in_batch:
            if i not in seen:
                new_list.append(i)
                seen.add(i)
        
        for i in existing:
            if i not in seen:
                new_list.append(i)
                seen.add(i)
                
        new_list = new_list[:40] 
        
        try:
            with open(path, 'w') as f: json.dump(new_list, f)
            self.log(f"Updated list {filename} with {len(self.fetched_ids_in_batch)} new items.")
        except Exception as e:
            self.log(f"Failed to save list {filename}: {e}")

    def get_smmdb_difficulty_id(self, diff_val):
        if diff_val is None: return 1 # Default Normal

        # Handle integers directly (API might return 0, 1, 2, 3)
        if isinstance(diff_val, int):
            if 0 <= diff_val <= 3:
                return diff_val
            return 1

        # Handle strings
        d = str(diff_val).lower().strip()
        if d == 'easy' or d == '0': return 0
        if d == 'normal' or d == '1': return 1
        if d == 'expert' or d == '2': return 2
        if d == 'superexpert' or d == 'super expert' or d == '3': return 3
        return 1

    def download_and_process(self, course):
        if 'id' not in course: return None

        course_id = course['id']
        index = get_next_index()
        dest_folder = BASE_CW_DIR if self.current_source_type == 'CourseWorld' else BASE_SMMDB_DIR
        
        # DETERMINE FOLDER BASED ON DIFFICULTY
        diff_id = 1 # Default Normal
        if self.current_source_type == 'SMMDB':
            # SMMDB API returns difficulty string OR int
            diff_id = self.get_smmdb_difficulty_id(course.get('difficulty', 'normal'))
        else:
            diff_id = 1

        basedir = os.path.join(dest_folder, str(diff_id))
        mkdir(basedir)

        # Precompute the formatted full ID for logging and saving
        full_id_str = f'{index:011d}-00001'

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

                if not download_success or not data: return None
                if data[:15].strip().lower().startswith(b'<!doctype'): return None

                separator = b'ASH0'
                starts = [m.start() for m in re.finditer(separator, data)]
                if len(starts) != 4: return None

                try:
                    chunks = [data[starts[i]:starts[i+1]] for i in range(3)] + [data[starts[3]:]]
                    for part in chunks:
                        if not ash0_decompress(part): raise ValueError("Corrupt")
                except: return None

                basename = os.path.join(basedir, full_id_str)
                with open(basename + '.json', 'w') as f: json.dump(course, f)
                with open(basename, 'wb') as f: 
                    f.write(data)
                    f.flush()
                    os.fsync(f.fileno())

                self.log(f"SAVED: {full_id_str}")
                return index

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
                basename = os.path.join(basedir, full_id_str)
                with open(basename + '.json', 'w') as f: json.dump(course, f)
                with open(basename, 'wb') as f: 
                    f.write(c1 + c2 + c3 + c4)
                    f.flush()
                    os.fsync(f.fileno())

                self.log(f"SAVED: {full_id_str}")
                return index
        except Exception as e:
            self.log(f"Failed {course_id}: {e}")
            return None

def start_cache_worker(p_queue, l_queue):
    mgr = CacheManager(p_queue, l_queue)
    mgr.start_worker()

if __name__ == "__main__":
    mgr = CacheManager()
    mgr.worker_loop()