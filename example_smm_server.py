
from nintendo.nex import backend, service, kerberos, \
    authentication, secure, datastoresmm, common, messagedelivery
from nintendo.games import SMM
import collections
import itertools
import secrets
import time
import argparse
import logging
import base64
import json
import jsons
import copy
from smm_dataprovider import SmmDataProvider
import pathlib


# https://stackoverflow.com/a/44175370/1806760
logging.basicConfig(
    format="[%(asctime)s] %(levelname)s: %(message)s",
    level=logging.INFO,
    datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger(__name__)

User = collections.namedtuple("User", "pid name password")

users = [
    User(1, "unknown", "password"),
    User(2, "Quazal Rendez-Vous", "password"),
    User(100, "guest", "MMQea3n!fsik"),
    User(1337, "1337", "password"),
    # More accounts here
]


def get_user_by_name(name):
    for user in users:
        if user.name == name:
            return user


def get_user_by_pid(pid):
    for user in users:
        if user.pid == pid:
            return user


def derive_key(settings, user):
    if settings.get("kerberos.key_derivation") == 0:
        key_derivation = kerberos.KeyDerivationOld(65000, 1024)
    else:
        key_derivation = kerberos.KeyDerivationNew(1, 1)
    return key_derivation.derive_key(user.password.encode("ascii"), user.pid)


SECURE_SERVER = "Quazal Rendez-Vous"


class AuthenticationServer(authentication.AuthenticationServer):
    def __init__(self, settings, secure_host, secure_port):
        super().__init__()
        self.settings = settings
        self.secure_host = secure_host
        self.secure_port = secure_port

    def login_ex(self, context, username, extra_data):
        return self.login(context, username)

    def login(self, context, username):
        logger.info("User trying to log in: {}".format(username))

        user = get_user_by_name(username)
        if not user:
            raise common.RMCError("RendezVous::InvalidUsername")

        server = get_user_by_name(SECURE_SERVER)

        url = common.StationURL(
            scheme="prudps", address=self.secure_host, port=self.secure_port,
            PID=server.pid, CID=1, type=2,
            sid=1, stream=10
        )

        conn_data = authentication.RVConnectionData()
        conn_data.server_time = common.DateTime.fromtimestamp(time.time())
        conn_data.main_station = url
        conn_data.special_protocols = []
        conn_data.special_station = common.StationURL()

        response = common.RMCResponse()
        response.result = common.Result(0x10001)  # Success
        response.pid = user.pid
        response.ticket = self.generate_ticket(user, server)
        response.connection_data = conn_data
        response.server_name = "branch:origin/project/wup-ama build:3_8_27_3022_0"
        return response

    def request_ticket(self, context, source, target):
        source = get_user_by_pid(source)
        target = get_user_by_pid(target)

        response = common.RMCResponse()
        response.result = common.Result(0x10001)  # Success
        response.ticket = self.generate_ticket(source, target)
        return response

    def generate_ticket(self, source, target):
        settings = self.settings

        user_key = derive_key(settings, source)
        server_key = derive_key(settings, target)
        session_key = secrets.token_bytes(settings.get("kerberos.key_size"))

        internal = kerberos.ServerTicket()
        internal.expiration = common.DateTime.fromtimestamp(time.time() + 120)
        internal.source_pid = source.pid
        internal.session_key = session_key

        ticket = kerberos.ClientTicket()
        ticket.session_key = session_key
        ticket.target_pid = 1
        ticket.internal = internal.encrypt(server_key, settings)

        return ticket.encrypt(user_key, settings)


class SecureConnectionServer(secure.SecureConnectionServer):
    def __init__(self):
        super().__init__()
        self.connection_id = itertools.count(10)

    def register(self, context, urls):
        addr = context.client.remote_address()
        station = urls[0].copy()
        station["address"] = addr[0]
        station["port"] = addr[1]
        station["type"] = 3

        response = common.RMCResponse()
        response.result = common.Result(0x10001)  # Success
        response.connection_id = next(self.connection_id)
        response.public_station = station
        return response

    def register_ex(self, context, urls, login_data):
        return self.register(context, urls)


class DataStoreSmmServer(datastoresmm.DataStoreSmmServer):
    def __init__(self, settings):
        super(DataStoreSmmServer, self).__init__()
        self.settings = settings
        self.data_provider = SmmDataProvider(self.settings)

    def get_meta(self, context, param):
        logger.info("param: %s" % json.dumps(jsons.dump(param)))
        if param.data_id == 0:  # mii data
            owner_id = param.persistence_target.owner_id
            res = self.data_provider.get_mii_data_pid(owner_id)
            if not res:
                logger.info("get_meta, no info for {}, using fake 1781058687".format(owner_id))
                res = self.data_provider.get_mii_data_pid(1781058687)
            res = copy.deepcopy(res.info)
            res.owner_id = param.persistence_target.owner_id
            res.tags = []
            res.ratings = []
            logger.info("res: %s" % json.dumps(jsons.dump(res)))
            return res
        elif param.data_id == 900000:  # when you click 'Play' this is requested, appears to be some dummy data
            res = datastoresmm.DataStoreMetaInfo()
            res.data_id = 900000
            res.owner_id = 2
            res.size = 450068
            res.name = ""
            res.data_type = 50
            res.meta_binary = b""
            res.permission = datastoresmm.DataStorePermission()
            res.permission.permission = 0
            res.permission.recipients = []
            res.delete_permission = datastoresmm.DataStorePermission()
            res.delete_permission.permission = 0
            res.delete_permission.recipients = []
            res.create_time = common.DateTime(0x1F7EC8FC86)
            res.update_time = common.DateTime(0x1F86A20516)
            res.period = 0xFB32
            res.status = 0
            res.referred_count = 0
            res.refer_data_id = 0
            res.flag = 0x100
            res.referred_time = res.create_time
            res.expire_time = common.DateTime(0x9C3F3E0000)
            res.tags = []
            res.ratings = []
            logger.info("res: %s" % json.dumps(jsons.dump(res)))
            return res
        else:
            logger.warning("DataStoreSmmServer.get_meta not implemented (data_id: {})".format(param.data_id))
            raise common.RMCError("DataStore::NotFound")

    def prepare_post_object(self, context, param):
        logger.info("param: %s" % json.dumps(jsons.dump(param)))
        info = datastoresmm.DataStoreReqPostInfo()
        info.data_id = 1337
        info.url = "http://account.nintendo.net/post"
        info.headers = []
        info.form = []
        info.root_ca_cert = b""
        return info

    def prepare_attach_file(self, context, param):
        logger.info("param: %s" % json.dumps(jsons.dump(param)))
        return self.prepare_post_object(context, param.unk1)

    def complete_attach_file(self, context, param):
        logger.info("param: %s" % json.dumps(jsons.dump(param)))
        return "kurwa"

    def complete_post_object(self, context, param):
        logger.info("param: %s" % json.dumps(jsons.dump(param)))

    def get_req_info(self, data_id):
        res = datastoresmm.DataStoreReqGetInfo()
        res.root_ca_cert = b""
        res.data_id = data_id
        res.headers = []
        if data_id == 900000:
            res.url = "http://account.nintendo.net/datastore/00000900000-00045"
            res.size = 450068  # hardcoded event course
        else:  # course download url by data_id
            course_data: datastoresmm.DataStoreInfoStuff
            course_data = self.data_provider.get_course_data(data_id)
            if not course_data:
                raise common.RMCError("DataStore::NotFound")
            res.url = self.data_provider.get_course_url(data_id)
            if not res.url:
                raise common.RMCError("DataStore::NotFound")
            res.size = course_data.info.size
        return res

    def prepare_get_object(self, context, param):
        logger.info("param: %s" % json.dumps(jsons.dump(param)))
        res = self.get_req_info(param.data_id)
        logger.info("res: %s" % json.dumps(jsons.dump(res)))
        return res

    def get_file_server_object_infos(self, context, params):
        logger.info("params: {}".format(json.dumps(params)))
        infos = []
        for data_id in params:
            info = datastoresmm.DataStoreFileServerObjectInfo()
            info.data_id = data_id
            info.info = self.get_req_info(data_id)
            infos.append(info)
        return infos

    def get_metas_multiple_param(self, context, params: [datastoresmm.DataStoreGetMetaParam]):
        logger.info("params: %s" % json.dumps(jsons.dump(params)))
        res = common.RMCResponse()
        res.result = common.Result(0x10001)  # Success
        res.infos = []
        res.results = []
        if not params:  # empty params -> empty response (wtf, smm actually sends this request)
            pass
        else:  # get mii data in bulk
            data: datastoresmm.DataStoreGetMetaParam
            for data in params:
                if data.result_option == 4:  # mii data
                    # TODO: somehow involve the disk database to have proper mii names for course creators
                    mii_data = self.data_provider.get_mii_data_pid(data.persistence_target.owner_id)
                    if not mii_data:
                        raise common.RMCError("DataStore::NotFound")
                    mii_data = copy.deepcopy(mii_data.info)
                    mii_data.tags = []
                    mii_data.ratings = []
                    res.infos.append(mii_data)
                    res.results.append(common.Result(0x690001))
                else:
                    raise common.RMCError("Core::NotImplemented")
        return res

    def change_meta(self, context, param):
        logger.info("param: %s" % json.dumps(jsons.dump(param)))

    def rate_objects(self, context, targets, params, transactional, fetch_ratings):
        """
        Adds to the rating slot for either course or mii info
        """
        logger.info("targets: %s\nparams: %s\ntransactional: %s\nfetch_ratings: %s" % (json.dumps(jsons.dump(targets)), json.dumps(jsons.dump(params)), transactional, fetch_ratings))
        res = common.RMCResponse()
        res.result = common.Result(0x10001)  # Success
        res.ratings = []
        res.results = []
        for i in range(0, len(targets)):
            rating = datastoresmm.DataStoreRatingInfo()
            rating.initial_value = 0
            rating.total_value = rating.count = params[i].rating_value
            res.results.append(common.Result(0x690001))
        return res

    def rate_custom_ranking(self, context, param):
        logger.info("param: %s" % json.dumps(jsons.dump(param)))

    def get_custom_ranking_by_data_id(self, context, param):
        """
        Appears to get Mii data, but with SMM ratings(?) and tags
        It also gets course metadata?

        Rating slot information (for Mii/player data):
        0: 1529 1529 -> courses played / courses played
        1: 1153 1153 -> courses cleared / courses cleared
        2: 608 608 -> unknown / unknown
        3: 5732 1529 -> total plays / courses played
        4: 4579 1529 -> lives lost / courses played
        5: 0 0 -> normal clears / normal clears
        6: 0 0 -> unknown
        7: 1 1 -> easy clears / easy clears
        8: 0 0 -> unknown
        Tag is a country string. Known values:
        ["1"] -> Japan
        ["97"] -> Poland
        ["49"] -> USA
        ["77"] -> France
        ["78"] -> Germany

        Rating slot information (for Courses):
        0: 40 40 plays / plays
        1: 843 40
        2: 38 40 clears / plays
        3: 59 40 attempts / plays
        4: 21 40 fails / plays
        5: 39 39 recent players / recent players
        6: 0 0 shared / shared (uncertain)
        """
        logger.info("param: %s" % json.dumps(jsons.dump(param)))
        if param.unk != 0x27:  # Game version?
            logger.info("unknown version!")
            raise common.RMCError("DataStore::InvalidArgument")

        res = common.RMCResponse()
        res.result = common.Result(0x10001)  # Success
        res.infos = []
        res.results = []

        def rating_slot(slot, total_value, count, initial_value):
            rating = datastoresmm.DataStoreRatingInfoWithSlot()
            rating.slot = slot
            rating.info = datastoresmm.DataStoreRatingInfo()
            rating.info.total_value = total_value
            rating.info.count = count
            rating.info.initial_value = initial_value
            return rating

        if param.magic == 300000000:  # Mii data with SMM ratings
            for data_id in param.data_ids:
                mii_data: datastoresmm.DataStoreInfoStuff
                mii_data = self.data_provider.get_mii_data_id(data_id)
                if not mii_data:
                    logger.info("get_custom_ranking_by_data_id(mii) unknown data_id: {}".format(data_id))
                    raise common.RMCError("DataStore::NotFound")
                res.infos.append(mii_data)
                res.results.append(common.Result(0x690001))
        elif param.magic == 0:  # Course metadata?
            if not param.data_ids:
                # TODO: implement (bookmarks?)
                param.data_ids = [10000000200]

            for data_id in param.data_ids:
                # definitely involve course metadata!
                course_data: datastoresmm.DataStoreInfoStuff
                course_data = self.data_provider.get_course_data(data_id)
                if not course_data:
                    logger.info("get_custom_ranking_by_data_id(course) unknown data_id: {}".format(data_id))
                    raise common.RMCError("DataStore::NotFound")
                res.infos.append(course_data)
                res.results.append(common.Result(0x690001))
        else:
            logger.info("unknown magic!")
            raise common.RMCError("DataStore::InvalidArgument")

        #logger.info("res: %s" % json.dumps(jsons.dump(res)))
        return res

    # called when a course is completed
    def add_to_buffer_queues(self, context, unknown1, unknown2):
        """
        if you die in a course your last death will be in unknown2[2]
        the first two parameters are unclear
        """
        logger.info("unknown1: %s\nunknown2: %s" % (json.dumps(jsons.dump(unknown1)), json.dumps(jsons.dump(unknown2))))
        res = []
        for i in range(0, len(unknown1)):
            res.append(common.Result(0x690001))
        return res

    # called if you click 'Course World'
    # also called before you start a course
    # it looks like coordinates of where people died (those red crosses)
    # this information appears to be provided by add_to_buffer_queues (in the third parameter of unknown2?)
    def get_buffer_queue(self, context, param):
        logger.info("param: %s" % json.dumps(jsons.dump(param)))
        if param.unk2 == 0:  # potentially data ids?
            #TODO: implement (starred courses)
            logger.info("unk2==0")
            res = []
            data_id = 10000000200  # data_id of starred course
            res.append(data_id.to_bytes(8, byteorder='little'))
        elif param.unk2 == 3:  # locations where players last died before they finished the course
            self.data_provider.mark_course_played(param.data_id)  # this also happens to be the point where you know someone played a course
            res = self.data_provider.get_unkdata(param.data_id)
            if res is None:
                logger.info("get_buffer_queue, fake unknown data (empty)")
                res = []
        else:
            logger.warning("DataStoreSmmServer.get_buffer_queue not implemented (data_id: {})".format(param.data_id))
            raise common.RMCError("DataStore::NotFound")
        logger.info("res: %s" % json.dumps(jsons.dump(res)))
        return res

    def get_application_config(self, context, param):
        logger.info("get_application_config({})".format(param))
        if param == 0:
            res = [0x1, 0x32, 0x96, 0x12C, 0x1F4, 0x320, 0x514, 0x7D0, 0xBB8, 0x1388, 0xA, 0x14, 0x1E, 0x28, 0x32, 0x3C, 0x46, 0x50, 0x5A, 0x64, 0x23, 0x4B, 0x23, 0x4B, 0x32, 0x0, 0x3, 0x3, 0x64, 0x6, 0x1, 0x60, 0x5, 0x60, 0x0, 0x7E4, 0x1, 0x1, 0xC, 0x0]
        elif param == 1:
            res = [0x2, 0x6982CC70, 0x6982CC50, 0x6982CC38, 0x6982D0DB, 0x6982D0A9, 0x6982D089, 0x6982C459, 0x6982C436]
        elif param == 2:
            res = [0x7DF, 0xC, 0x16, 0x5, 0x0]
        else:
            raise common.RMCError("DataStore::InvalidArgument")
        logger.info("res: {}".format(repr(res)))
        return res

    def recommended_course_search_object(self, context, unknown1, unknown2):
        """
        Function related to 100 Mario Challenge and course browser
        Observed values:
        ["1", "0", "34", "", "0"]   # easy
        ["1", "0", "29", "", "0"]   # easy(?) (conditions unknown)
        ["1", "0", "74", "", "0"]   # normal
        ["1", "0", "69", "", "0"]   # normal(?) (conditions unknown)
        ["1", "75", "95", "", "0"]  # expert
        ["1", "70", "100", "", "0"] # expert(?) (conditions unknown)
        ["1", "96", "100", "", "0"] # super expert
        Rambo6Glaz derived the following:
        Maybe it’s the minimum and maximum clear rate for the difficulty
        Or fail rate
        Easy: 0-34% fails
        Normal: 0-74% people failed
        Expert: 75-95%
        Super expert: 95-100%
        It would actually make sense considering the average success rate for expert and super-expert.
        For the last course of easy it looks like:
        ["1", "0", "34", "", "0", "12"]
        so far it is unclear what this parameter is about

        When using course browser (highlights):
        ["", "", "", "0", "0"]       # Filter: all
        ["1", "0", "34", "0", "0"]   # Filter: easy
        ["1", "35", "74", "0", "0"]  # Filter: normal
        ["1", "75", "95", "0", "0"]  # Filter: expert
        ["1", "96", "100", "0", "0"] # Filter: super expert
        """
        logger.info("unknown1: %s\nunknown2: %s" % (json.dumps(jsons.dump(unknown1)), json.dumps(jsons.dump(unknown2))))

        if len(unknown2) >= 5 and unknown2[0] == "1" and unknown2[4] == "0":
            fail_rate_min, fail_rate_max = int(unknown2[1]), int(unknown2[2])
            logger.info("min: {}, max: {}".format(fail_rate_min, fail_rate_max))
            if unknown2[3] == "":
                # Little bit of a hack, in the wild different values have been observed (see method comment).
                # The workaround is to check if the difficulty parameter is 'close', which might work.
                def is_close(a, b):
                    return abs(a - b) <= 10

                logger.info("detected 100 mario")
                if is_close(fail_rate_min, 0) and is_close(fail_rate_max, 34):  # easy
                    difficulty = 0
                elif is_close(fail_rate_min, 0) and is_close(fail_rate_max, 74):  # normal
                    difficulty = 1
                elif is_close(fail_rate_min, 75) and is_close(fail_rate_max, 95):  # expert
                    difficulty = 2
                elif is_close(fail_rate_min, 96) and is_close(fail_rate_max, 100):  # super expert
                    difficulty = 3
                else:
                    raise common.RMCError("DataStore::InvalidArgument")

                # Use the random course ids from disk
                return self.data_provider.get_random_courses_by_difficulty(difficulty, 50)
            elif unknown2[3] == "0":
                logger.info("detected course browser (highlights, normal)")
                return self.data_provider.get_random_courses_by_difficulty(1, 10)
        else:
            logger.info("recommended_course_search_object with unexpected unknown2 parameter")
            raise common.RMCError("DataStore::InvalidArgument")

    def followings_latest_course_search_object(self, context, unknown1, unknown2):
        logger.info("unknown1: {}\nunknown2: {}".format(json.dumps(jsons.dump(unknown1)), json.dumps(jsons.dump(unknown2))))
        if unknown2 == ["0", "3", "10", "4", "11", "5", "12", "6", "13", "7", "14", "8", "15"]:
            if unknown1.pids == [1337]:
                # TODO: implement (uploaded course metadata)
                res = []
                return res
            else:
                logger.info("followings_latest_course_search_object with unknown pids {}".format(unknown1.pids))
                raise common.RMCError("DataStore::NotFound")
        else:
            logger.info("followings_latest_course_search_object with unexpected unknown2 parameter")
            raise common.RMCError("DataStore::InvalidArgument")

    def upload_course_record(self, context, param):
        logger.info("param: {}".format(json.dumps(jsons.dump(param))))

    def get_course_record(self, context, param):
        """
        Method that fetched the current record
        """
        logger.info("get_course_record({})".format(json.dumps(jsons.dump(param))))
        if param.unk2 == 0:
            ranking = self.data_provider.get_ranking(param.data_id)
            if not ranking:
                logger.info("get_course_record, fake ranking")
                res = datastoresmm.CourseRecordInfo()
                res.data_id = param.data_id
                res.unk2 = 0  # uncleared?
                res.first_clear_pid = 1781058687
                res.world_record_pid = 1781058687
                res.world_record = 40320
                res.first_clear_date = common.DateTime(0x6A28CC7F)  # or null for uncleared date?
                res.world_record_date = common.DateTime(0x6A28CC7F)
                return res
            else:
                return ranking
        else:
            logger.info("get_course_record with unexpected unk2")
            raise common.RMCError("DataStore::InvalidArgument")

    def get_application_config_string(self, context, param):
        """
        Probably a function related to a word blacklist.
        """
        logger.info("param: %u (0x%X)" % (param, param))
        if param == 128:
            res = [u"けされ", u"消され", u"削除され", u"リセットされ", u"BANされ", u"ＢＡＮされ", u"キミのコース", u"君のコース", u"きみのコース", u"い い ね", u"遊びます", u"地震", u"震災", u"被災", u"津波", u"バンされ", u"い~ね", u"震度", u"じしん", u"banされ", u"くわしくは", u"詳しくは", u"ちんちん", u"ち0こ", u"bicth", u"い.い．ね", u"ナイ～ス", u"い&い", u"い-いね", u"いぃね", u"nigger", u"ngger", u"star if u", u"Star if u", u"Star if you", u"star if you", u"PENlS", u"マンコ", u"butthole", u"LILI", u"vagina", u"vagyna", u"うんち", u"うんこ", u"ウンコ", u"Ｉｉｎｅ", u"EENE", u"まんこ", u"ウンチ", u"niglet", u"nigglet", u"please like", u"きんたま", u"Butthole", u"llね", u"iいね", u"give a star", u"ちんぽ", u"亀頭", u"penis", u"ｳﾝｺ", u"plz more stars", u"star plz", u"い()ね", u"PLEASE star", u"Bitte Sterne"]
        elif param == 129:
            res = [u"ゼロから", u"０から", u"0から", u"い　　い　　ね", u"いい", u"東日本", u"大震"]
        elif param == 130:
            res = [u"いいね", u"下さい", u"ください", u"押して", u"おして", u"返す", u"かえす", u"星", u"してくれ", u"するよ", u"☆くれたら", u"☆あげます", u"★くれたら", u"★あげます", u"しね", u"ころす", u"ころされた", u"アナル", u"ファック", u"キンタマ", u"○ね", u"キチガイ", u"うんこ", u"KITIGAI", u"金玉", u"おっぱい", u"☆おす", u"☆押す", u"★おす", u"★押す", u"いいする", u"いいよ", u"イイネ", u"ケツ", u"うんち", u"かくせいざい", u"覚せい剤", u"シャブ", u"きんたま", u"ちんちん", u"おしっこ", u"ちんぽこ", u"ころして", u"グッド", u"グット", u"レ●プ", u"バーカ", u"きちがい", u"ちんげ", u"マンコ", u"まんこ", u"チンポ", u"クズ", u"ウンコ", u"ナイスおねがいします", u"penis", u"イイね", u"☆よろ"]
        else:
            logger.info("get_application_config_string with unknown parameter")
            raise common.RMCError("DataStore::InvalidArgument")
        return res

    def get_metas_with_course_record(self, context, unknown, get_meta_param):
        logger.info("unknown: {}".format(json.dumps(jsons.dump(unknown))))
        logger.info("get_meta_param: {}".format(json.dumps(jsons.dump(get_meta_param))))
        res = common.RMCResponse()
        res.result = common.Result(0x10001)  # Success
        res.infos = []
        res.unknown = []
        res.results = []
        if unknown == [] and get_meta_param.data_id == 0 and get_meta_param.result_option == 4:
            # TODO: implement (nobody knows)
            res.infos.append(self.data_provider.get_course_data(10000000200))

            record = datastoresmm.CourseRecordInfo()
            record.data_id = 10000000200  # TODO
            record.unk2 = 0  # uncleared?
            record.first_clear_pid = 1781058687
            record.world_record_pid = 1781058687
            record.world_record = 40320
            record.first_clear_date = common.DateTime(0x6A28CC7F)  # or null for uncleared date?
            record.world_record_date = common.DateTime(0x6A28CC7F)
            res.unknown.append(record)

            res.results.append(common.Result(0x690001))
            pass
        else:
            logger.info("UNSUPPORTED")
        logger.info("res: %s" % json.dumps(jsons.dump(res)))
        return res

    def check_rate_custom_ranking_counter(self, context, unk1):
        logging.info("dummy check_rate_custom_ranking_counter({})".format(unk1))
        return True

    def get_deletion_reason(self, context, param):
        logger.info("param: {}".format(json.dumps(jsons.dump(param))))
        res = []
        for data_id in param:
            res.append(0x80690007)
        return res


class MessageDeliveryServer(messagedelivery.MessageDeliveryServer):
    def __init__(self, settings):
        super(MessageDeliveryServer, self).__init__()
        self.settings = settings

    def deliver_message(self, context, message):
        logger.info("message: {}".format(json.dumps(jsons.dump(message))))


common.DataHolder.register(common.Data, "BinaryMessage")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("-host", default="127.0.0.1", help="hostname/ip to host the server on")
    parser.add_argument("-pid", type=int, help="additional user pid")
    parser.add_argument("-username", help="additional user username")
    parser.add_argument("-password", help="additional user password")
    args = parser.parse_args()
    host = args.host
    if args.pid and args.username and args.password:
        users.append(User(args.pid, args.username, args.password))

    settings = backend.Settings("default.cfg")
    settings.set("nex.access_key", SMM.ACCESS_KEY)
    settings.set("nex.version", SMM.NEX_VERSION)
    settings.set("prudp.ping_timeout", 10.0)

    secure_server_port = 59921
    server_key = derive_key(settings, get_user_by_name(SECURE_SERVER))
    secure_server = service.RMCServer(settings)
    secure_server.register_protocol(SecureConnectionServer())
    secure_server.register_protocol(DataStoreSmmServer(settings))
    secure_server.register_protocol(MessageDeliveryServer(settings))
    secure_server.start(host, secure_server_port, key=server_key)
    logger.info("smm secure server {}:{}".format(host, secure_server_port))

    auth_server_port = 59900
    auth_server = service.RMCServer(settings)
    auth_server.register_protocol(AuthenticationServer(settings, host, secure_server_port))
    auth_server.start(host, auth_server_port)
    logger.info("smm auth server {}:{}".format(host, auth_server_port))

    input("Press enter to exit...\n")


if __name__ == "__main__":
    main()
