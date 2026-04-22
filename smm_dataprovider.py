import random
from nintendo.nex import datastoresmm, common
import smmdb
import jsons
import json
import os
import copy
import struct
import re
import base64
import hmac
import hashlib
import zlib
import logging
import sys
import configparser

logger = logging.getLogger(__name__)

try:
    if not hasattr(hmac, '_patched_by_smm'):
        original_hmac_new = hmac.new
        def patched_hmac_new(key, msg=None, digestmod=None):
            if digestmod is None: digestmod = hashlib.md5
            return original_hmac_new(key, msg, digestmod)
        hmac.new = patched_hmac_new
        hmac._patched_by_smm = True
except Exception: pass

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))

def read_file(file):
    try:
        with open(os.path.join(CURRENT_DIR, file), "rb") as f: return f.read()
    except FileNotFoundError:
        return b""

smm_mario100 = read_file("smm_mario100.bin")
smm_miidata = read_file("smm_miidata.bin")
miidata2 = base64.b64decode("""AB4CAAAAAAAAAAAAAAARAgAAYacvAgAAAAB/zChqAAAAAAkAaXdoczEwODQAAQCM
AEJQRkMAAAABAAAAAAAAAAAAAAAAAAEAAAMAADBaxrslIMRw8JQm6C+4rm7VkAQA
AAAAXzBLMGgwAAAAAAAAAAAAAAAAAABHNwAAIQECZKQYIEVGFIESF2gNAAApAlFI
UAAAAAAAAAAAAAAAAAAAAAAAAAAAAAC/7gAAAAAAAAAAAAAAAAAAAAAAAAAAAAUA
AAAAAAAAAAAFAAAAAwAAAAAnjDqBHwAAACeMOoEfAAAAWgAAAAAAAAAAAAAAAQAA
J4w6gR8AAAAAAD4/nAAAAAEAAAACADEACQAAAAAaAAAAAAAUAAAAuQoAAAAAAAC5
CgAAAAAAAAAAAAAAGgAAAAEAFAAAAEsGAAAAAAAASwYAAAAAAAAAAAAAABoAAAAC
ABQAAAADAwAAAAAAAAMDAAAAAAAAAAAAAAAaAAAAAwAUAAAA4SAAAAAAAAC5CgAA
AAAAAAAAAAAAGgAAAAQAFAAAAJYaAAAAAAAAuQoAAAAAAAAAAAAAABoAAAAFABQA
AAAhAAAAAAAAACEAAAAAAAAAAAAAAAAaAAAABgAUAAAAAAAAAAAAAAAAAAAAAAAA
AAAAAAAAGgAAAAcAFAAAABoAAAAAAAAAGgAAAAAAAAAAAAAAABoAAAAIABQAAAAA
AAAAAAAAAAAAAAAAAAAAAAAAAA==""")
smm_coursedata = read_file("smm_coursedata.bin")
smm_unkdata = read_file("smm_unkdata.bin")
smm_rankings = read_file("smm_rankings.bin")

class SmmDataProvider:
    def __init__(self, settings, cache_manager=None):
        self.settings = settings
        self.mario100 = self.init_mario100_data()
        self.mii_data_id, self.mii_data_pid = self.init_mii_data()
        self.course_data = self.init_course_data()
        self.unkdata = self.init_unkdata()
        self.rankings = self.init_rankings()
        self.fake_mii_data_id = 20000000000
        self.fake_mii_pid = 2000000000
        self.fake_mii_name = {}
        self.list_offsets = {} 

    def get_active_source_dirs(self):
        """
        Reads settings.ini to determine which folders to scan.
        This prevents mixing SMMDB courses with CourseWorld courses.
        """
        config = configparser.ConfigParser()
        # Use SETTINGS_INI_PATH from smmdb to handle PyInstaller correctly
        ini_path = smmdb.SETTINGS_INI_PATH
        
        source = 'SMMDB' # Default
        try:
            if os.path.exists(ini_path):
                config.read(ini_path)
                source = config.get('General', 'CourseSource', fallback='SMMDB')
        except: pass

        if source == 'CourseWorld':
            return [smmdb.BASE_CW_DIR]
        else:
            return [smmdb.BASE_SMMDB_DIR]

    def init_mario100_data(self):
        if not smm_mario100: return []
        try:
            stream = common.streams.StreamIn(smm_mario100, self.settings)
            return stream.list(datastoresmm.DataStoreInfoStuff)
        except: return []

    def init_mii_data(self):
        mii_data_id, mii_data_pid = {}, {}
        try:
            infos = []
            if smm_miidata:
                stream = common.streams.StreamIn(smm_miidata, self.settings)
                infos = stream.list(datastoresmm.DataStoreInfoStuff)
            
            try:
                stream = common.streams.StreamIn(miidata2, self.settings)
                infos.append(stream.extract(datastoresmm.DataStoreInfoStuff))
            except: pass

            for info in infos:
                mii_data_id[info.info.data_id] = info
                mii_data_pid[info.info.owner_id] = info.info.data_id
        except: pass
        return mii_data_id, mii_data_pid

    def init_course_data(self):
        if not smm_coursedata: return {}
        try:
            stream = common.streams.StreamIn(smm_coursedata, self.settings)
            infos = stream.list(datastoresmm.DataStoreInfoStuff)
            return {info.info.data_id: info for info in infos if info.info.data_id != 21340114}
        except: return {}

    def init_unkdata(self):
        if not smm_unkdata: return {}
        try:
            stream = common.streams.StreamIn(smm_unkdata, self.settings)
            count = stream.u32()
            return {stream.u64(): stream.list(stream.qbuffer) for _ in range(count)}
        except: return {}

    def init_rankings(self):
        if not smm_rankings: return {}
        try:
            stream = common.streams.StreamIn(smm_rankings, self.settings)
            return {ranking.data_id: ranking for ranking in stream.list(datastoresmm.CourseRecordInfo)}
        except: return {}

    def get_mario100_data(self): return self.mario100

    def _generate_dummy_mii(self, pid, name="Player"):
        if pid in self.mii_data_pid:
            return self.mii_data_id[self.mii_data_pid[pid]]
        
        if self.mii_data_id:
            base_key = next(iter(self.mii_data_id))
            template = self.mii_data_id[base_key]
        else:
            return None 

        new_data_id = self.fake_mii_data_id
        self.fake_mii_data_id += 1
        
        fake_mii = copy.deepcopy(template)
        fake_mii.info.data_id = new_data_id
        fake_mii.info.owner_id = pid
        fake_mii.info.name = name
        
        self.mii_data_id[new_data_id] = fake_mii
        self.mii_data_pid[pid] = new_data_id
        return fake_mii

    def get_mii_data_pid(self, pid):
        if pid in self.mii_data_pid: return self.mii_data_id[self.mii_data_pid[pid]]
        
        official_pids = [1770179696, 1770179664, 1770179640, 1770180827, 1770180777, 1770180745, 1770177625, 1770177590]
        if pid in official_pids and self.mii_data_pid:
            mii_pids = list(self.mii_data_pid)
            idx = official_pids.index(pid) % len(mii_pids)
            fake_pid = mii_pids[idx]
            data = copy.deepcopy(self.mii_data_id[self.mii_data_pid[fake_pid]])
            data.owner_id = pid
            return data
            
        return self._generate_dummy_mii(pid, f"Player {pid}")

    def get_mii_data_id(self, data_id): return self.mii_data_id.get(data_id)

    def construct_fake_miidata(self, name):
        if name in self.fake_mii_name: return self.fake_mii_name[name]
        data_id = self.fake_mii_data_id; self.fake_mii_data_id += 1
        pid = self.fake_mii_pid; self.fake_mii_pid += 1
        
        if not self.mii_data_id: return pid
        
        real_keys = list(self.mii_data_id.keys())
        base_key = real_keys[abs(hash(name)) % len(real_keys)]
        fake_mii = copy.deepcopy(self.mii_data_id[base_key])
        fake_mii.info.data_id = data_id; fake_mii.info.owner_id = pid; fake_mii.info.name = name
        self.mii_data_id[data_id] = fake_mii; self.mii_data_pid[pid] = data_id
        self.fake_mii_name[name] = pid
        return pid

    def construct_fake_coursedata(self, course_id, filepath):
        try:
            with open(filepath, 'rb') as f: binary_data = f.read()
            with open(filepath + '.json', 'r') as f: meta_json = jsons.loads(f.read())
            
            info = datastoresmm.DataStoreInfoStuff()
            info.stars_received = meta_json.get("stars", 0)
            
            meta = datastoresmm.DataStoreMetaInfo()
            meta.data_id = course_id
            
            maker_name = meta_json.get("maker", "Player")
            meta.owner_id = self.construct_fake_miidata(maker_name)
            
            meta.name = meta_json.get("title", "Course")
            meta.data_type = 6
            meta.size = len(binary_data)
            
            if "meta_binary_b64" in meta_json:
                meta.meta_binary = base64.b64decode(meta_json["meta_binary_b64"])
            else:
                starts = [m.start() for m in re.finditer(b'ASH0', binary_data)]
                if len(starts) != 4: return None 
                chunks = [binary_data[starts[i]:starts[i+1]] for i in range(3)] + [binary_data[starts[3]:]]
                theme = meta_json.get("courseTheme", 0)
                crcs = [zlib.crc32(c) for c in chunks]
                meta.meta_binary = struct.pack(">IIIIIIIIIII", 1, theme, len(chunks[1]), len(chunks[2]), len(chunks[0]), len(chunks[3]), 1, crcs[1], crcs[2], crcs[0], crcs[3])
            
            perm = datastoresmm.DataStorePermission(); perm.permission = 0; perm.recipients = []
            meta.permission = perm
            del_perm = datastoresmm.DataStorePermission(); del_perm.permission = 3; del_perm.recipients = []
            meta.delete_permission = del_perm
            meta.create_time = common.DateTime(0); meta.update_time = common.DateTime(0)
            meta.referred_time = common.DateTime(0); meta.expire_time = common.DateTime(0)
            meta.tags = [""]; meta.ratings = []
            info.info = meta
            return info
        except Exception as e:
            logger.error(f"Error constructing course {course_id}: {e}")
            return None

    def get_course_data(self, data_id):
        if data_id in self.course_data: return self.course_data[data_id]
        filename = self.get_course_filename(data_id)
        if filename:
            data = self.construct_fake_coursedata(data_id, filename)
            if data:
                self.course_data[data_id] = data
                return data
        return None

    def get_course_filename(self, data_id):
        expected = "{:011d}-00001".format(data_id)
        
        # Only check the active source folder!
        active_bases = self.get_active_source_dirs()
        
        for base in active_bases:
            for i in range(4):
                path = os.path.join(base, str(i))
                if os.path.exists(path):
                    fpath = os.path.join(path, expected)
                    if os.path.exists(fpath):
                        return fpath
        return None
        
    def get_course_url(self, data_id):
        ip = "127.0.0.1" if sys.platform == 'darwin' else "127.0.5.1"
        return f"http://{ip}:8383/smm/course/{data_id}"

    def mark_course_played(self, data_id):
        filename = self.get_course_filename(data_id)
        if filename and not os.path.exists(filename + ".played"):
            try: open(filename + ".played", 'a').close()
            except: pass

    def get_courses_from_list(self, list_name, limit=10):
        try:
            path = os.path.join(smmdb.LISTS_DIR, list_name)
            if not os.path.exists(path):
                return self.get_random_courses_mixed(limit)
                
            with open(path, 'r') as f:
                ids = json.load(f)
            
            if not ids:
                return self.get_random_courses_mixed(limit)

            current_offset = self.list_offsets.get(list_name, 0)
            
            if current_offset + limit <= len(ids):
                selected_ids = ids[current_offset : current_offset + limit]
                self.list_offsets[list_name] = (current_offset + len(selected_ids)) % len(ids)
            else:
                chunk1 = ids[current_offset:]
                remaining = limit - len(chunk1)
                chunk2 = ids[:remaining]
                selected_ids = chunk1 + chunk2
                self.list_offsets[list_name] = remaining

            result = []
            for cid in selected_ids:
                data = self.get_course_data(cid)
                if data:
                    result.append(data)
            
            if len(result) < limit:
                needed = limit - len(result)
                result.extend(self.get_random_courses_mixed(needed))
                
            return result

        except Exception as e:
            logger.error(f"Error reading list {list_name}: {e}")
            return self.get_random_courses_mixed(limit)

    def get_new_arrivals(self):
        return self.get_courses_from_list("new_arrivals.json", 10)

    def get_star_ranking(self):
        return self.get_courses_from_list("star_ranking.json", 10)

    def get_random_courses_mixed(self, amount):
        """
        Mixes courses from ALL difficulties for Highlights, excluding those already in lists.
        Only scans the active source type.
        """
        bases_to_check = self.get_active_source_dirs()
        candidates = []
        
        excluded_ids = set()
        for list_name in ["new_arrivals.json", "star_ranking.json"]:
            try:
                path = os.path.join(smmdb.LISTS_DIR, list_name)
                if os.path.exists(path):
                    with open(path, 'r') as f:
                        ids = json.load(f)
                        excluded_ids.update(ids)
            except: pass

        for base in bases_to_check:
            for diff in range(4):
                path = os.path.join(base, str(diff))
                if os.path.exists(path):
                    for f in os.listdir(path):
                        if f.endswith('-00001'):
                            try:
                                cid = int(f.split('-')[0])
                                if cid == 10000000200 or cid in excluded_ids:
                                    continue
                                candidates.append(cid)
                            except: continue

        if not candidates:
            return []
        
        sample_ids = random.sample(candidates, min(amount, len(candidates)))
        result = []
        for cid in sample_ids:
            data = self.get_course_data(cid)
            if data:
                result.append(data)
        return result

    def get_random_courses_by_difficulty(self, difficulty, amount):
        """
        Strict difficulty fetch for 100 Mario.
        Only scans the active source type.
        """
        diff_value = difficulty.value if hasattr(difficulty, 'value') else difficulty
        bases_to_check = self.get_active_source_dirs()
        
        unplayed_candidates = []
        played_candidates = []
        
        for base in bases_to_check:
            path = os.path.join(base, str(diff_value))
            if os.path.exists(path):
                for f in os.listdir(path):
                    if f.endswith('-00001'):
                        try:
                            cid = int(f.split('-')[0])
                            if cid == 10000000200: continue
                            
                            course_path = os.path.join(path, f)
                            if not os.path.exists(course_path + '.played'):
                                unplayed_candidates.append(course_path)
                            else:
                                played_candidates.append(course_path)
                        except: continue

        selection = []
        if len(unplayed_candidates) >= amount:
            selection = random.sample(unplayed_candidates, amount)
        else:
            selection = unplayed_candidates
            remaining = amount - len(selection)
            
            if played_candidates:
                fill_amount = min(remaining, len(played_candidates))
                selection.extend(random.sample(played_candidates, fill_amount))

        if not selection:
            return []
            
        random.shuffle(selection)
        
        result = []
        for path in selection:
            try:
                cid = int(os.path.basename(path).split('-')[0])
                data = self.get_course_data(cid)
                if data:
                    result.append(data)
            except Exception:
                continue
            
        return result

    def get_unkdata(self, data_id): return self.unkdata.get(data_id)
    def get_ranking(self, data_id): return self.rankings.get(data_id)