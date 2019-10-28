import random

from nintendo.nex import backend, service, kerberos, \
    authentication, secure, datastoresmm, common

from nintendo.miis import MiiData

import base64
import smmdb
import jsons
import array
import os
import copy

def read_file(file):
    f = open(file, "rb")
    data = f.read()
    f.close()
    return data


smm_mario100 = read_file("smm_mario100.bin")
smm_miidata = read_file("smm_miidata.bin")
# hardcoded mii 1781058687
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
    def __init__(self, settings):
        self.settings = settings
        self.mario100 = self.init_mario100_data()
        self.mii_data_id, self.mii_data_pid = self.init_mii_data()
        self.course_data = self.init_course_data()
        self.unkdata = self.init_unkdata()
        self.rankings = self.init_rankings()
        self.fake_mii_data_id = 20000000000
        self.fake_mii_pid = 2000000000
        self.fake_mii_name = {}
        self.smmdb_queue = smmdb.SmmdbQueue()
        self.smmdb_queue.fetch_async()
        self.smmdb_queue.wait()

    def init_mario100_data(self):
        global smm_mario100
        stream = common.streams.StreamIn(smm_mario100, self.settings)
        return stream.list(datastoresmm.DataStoreInfoStuff)

    def init_mii_data(self):
        global smm_miidata
        global miidata2
        stream = common.streams.StreamIn(smm_miidata, self.settings)
        infos = stream.list(datastoresmm.DataStoreInfoStuff)
        stream = common.streams.StreamIn(miidata2, self.settings)
        stuff = stream.extract(datastoresmm.DataStoreInfoStuff)
        infos.append(stuff)
        mii_data_id, mii_data_pid = {}, {}

        info: datastoresmm.DataStoreInfoStuff
        for info in infos:
            mii_data_id[info.info.data_id] = info
            mii_data_pid[info.info.owner_id] = info.info.data_id
        return mii_data_id, mii_data_pid

    def init_course_data(self):
        global smm_coursedata
        stream = common.streams.StreamIn(smm_coursedata, self.settings)
        infos = stream.list(datastoresmm.DataStoreInfoStuff)
        course_data = {}
        info: datastoresmm.DataStoreInfoStuff
        for info in infos:
            if info.info.data_id == 21340114:
                continue
            course_data[info.info.data_id] = info
        return course_data

    def init_unkdata(self):
        global smm_unkdata
        stream = common.streams.StreamIn(smm_unkdata, self.settings)
        count = stream.u32()
        unkdata = {}
        for i in range(0, count):
            data_id = stream.u64()
            buffers = stream.list(stream.qbuffer)
            unkdata[data_id] = buffers
        return unkdata

    def init_rankings(self):
        global smm_rankings
        stream = common.streams.StreamIn(smm_rankings, self.settings)
        rankings = {}
        infos = stream.list(datastoresmm.CourseRecordInfo)
        ranking: datastoresmm.CourseRecordInfo
        for ranking in infos:
            rankings[ranking.data_id] = ranking
        return rankings

    def get_mario100_data(self):
        return self.mario100

    def get_mii_data_pid(self, pid):
        if pid in self.mii_data_pid:
            return self.mii_data_id[self.mii_data_pid[pid]]
        return None

    def get_mii_data_id(self, data_id):
        if data_id in self.mii_data_id:
            return self.mii_data_id[data_id]
        return None

    def rename_fake_mii(self, fake_mii, newname):
        """
        struct BPFC {
            uint32 magic; //BPFC
            uint32 unk1;
            uint32 unk2;
            uint32 unk3;
            uint32 unk4;
            uint32 unk5;
            uint8 miidata[96];
            uint32 unk6;
            uint32 unk7;
            uint32 unk8;
            uint32 unk9;
            uint32 unk10;
        };
        """
        meta_binary = array.array("B")
        meta_binary.extend(fake_mii.info.meta_binary)
        mii_binary = meta_binary[6 * 4:][:96]

        mii = MiiData.parse(mii_binary)
        mii.mii_name = newname.replace("%", "").replace("\\", "")
        fake_mii_binary = mii.build()
        for i in range(0, len(fake_mii_binary)):
            meta_binary[i + 6 * 4] = fake_mii_binary[i]
        fake_mii.info.meta_binary = meta_binary.tobytes()

    def construct_fake_miidata(self, name):
        if name in self.fake_mii_name:
            return self.fake_mii_name[name]

        data_id = self.fake_mii_data_id
        self.fake_mii_data_id += 1
        pid = self.fake_mii_pid
        self.fake_mii_pid += 1

        real_keys = list(self.mii_data_id.keys())
        base_key = real_keys[abs(hash(name)) % len(real_keys)]
        fake_mii = copy.deepcopy(self.mii_data_id[base_key])
        fake_mii.info.data_id = data_id
        fake_mii.info.owner_id = pid
        fake_mii.info.name = name  # account id

        self.rename_fake_mii(fake_mii, name)

        self.mii_data_id[data_id] = fake_mii
        self.mii_data_pid[pid] = data_id
        self.fake_mii_name[name] = pid
        return pid

    def construct_fake_coursedata(self, course_id, coursefile):
        with open(coursefile + ".json", "r") as f:
            json = f.read()
        diskmeta = jsons.loads(json)

        meta_binary = array.array("B")
        meta_binary.extend(diskmeta["meta_binary"])
        meta_binary = meta_binary.tobytes()
        assert(len(meta_binary) == 44)

        info = datastoresmm.DataStoreInfoStuff()
        info.unk1 = 0  # as observed in real data
        info.stars_received = diskmeta["stars"]
        meta = datastoresmm.DataStoreMetaInfo()
        meta.data_id = course_id
        meta.owner_id = self.construct_fake_miidata(diskmeta["maker"])  # TODO: do proper on-disk storage of fake Miis
        meta.size = diskmeta["size"]
        meta.name = diskmeta["title"]
        meta.data_type = 6
        meta.meta_binary = meta_binary
        permission = datastoresmm.DataStorePermission()
        permission.permission = 0
        permission.recipients = []
        meta.permission = permission
        delete_permission = datastoresmm.DataStorePermission()
        delete_permission.permission = 3
        delete_permission.recipients = []
        meta.delete_permission = delete_permission
        meta.create_time = common.DateTime(diskmeta["uploaded"])
        meta.update_time = common.DateTime(diskmeta["lastmodified"])
        meta.period = 0
        meta.status = 0
        meta.referred_count = 0
        meta.refer_data_id = 0
        meta.flag = 0
        meta.referred_time = common.DateTime(135517191018)  # TODO: implement
        meta.expire_time = common.DateTime(135517191018)  # TODO: implement
        meta.tags = [""]
        meta.ratings = []  # TODO: implement
        info.info = meta
        return info

    def get_course_data(self, data_id):
        if data_id in self.course_data:
            return self.course_data[data_id]

        expected_filename = "{:011d}-00001".format(data_id)
        for file in smmdb.get_course_files("www/smmdb", recursive=True):
            if file.endswith(expected_filename):
                course_data = self.construct_fake_coursedata(data_id, file)
                if course_data:
                    self.course_data[data_id] = course_data  # cache result
                return course_data

        return None

    def get_course_filename(self, data_id):
        expected_filename = "{:011d}-00001".format(data_id)
        for file in smmdb.get_course_files("www/smmdb", recursive=True):
            if file.endswith(expected_filename):
                return file
        return None

    def get_course_url(self, data_id):
        course_filename = self.get_course_filename(data_id)
        if course_filename:
            return "http://account.nintendo.net/" + course_filename[4:].replace("\\", "/")
        return None

    def mark_course_played(self, data_id):
        course_filename = self.get_course_filename(data_id)
        if course_filename:
            played_filename = course_filename + ".played"
            if not os.path.exists(played_filename):
                open(played_filename, 'a').close()
                self.smmdb_queue.fetch_async()

    def get_random_courses_by_difficulty(self, difficulty, amount):
        unplayed_course_ids = []
        for file in smmdb.get_course_files("www/smmdb/{}".format(difficulty)):
            if not os.path.exists(file + ".played"):
                filename = os.path.basename(file)
                index = int(filename[:filename.index('-00001')])
                unplayed_course_ids.append(index)
        random_sample = random.sample(unplayed_course_ids, amount)
        return [self.get_course_data(index) for index in random_sample]

    def get_unkdata(self, data_id):
        if data_id in self.unkdata:
            return self.unkdata[data_id]
        return None

    def get_ranking(self, data_id):
        if data_id in self.rankings:
            return self.rankings[data_id]
        return None
