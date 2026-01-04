from nintendo.common import streams, util
import struct

def swap32(data, offs):
    struct.pack_into("<I", data, offs, struct.unpack_from(">I", data, offs)[0])

def swap16(data, offs):
    struct.pack_into("<H", data, offs, struct.unpack_from(">H", data, offs)[0])

class MiiData:
    def decode(self, stream):
        data = stream.read(0x60) 
        
        try:
            if util.crc16(data) != 0:
                print(f"[Warning] Mii data checksum invalid. Mii parsing aborted.")
                return None
        except Exception as e:
            print(f"[Warning] Error during Mii checksum verification: {e}. Mii parsing aborted.")
            return None

        stream = streams.BitStreamIn(self.swap_endian(data), ">")

        self.birth_platform = stream.bits(4)
        self.unk1 = stream.bits(4)
        self.unk2 = stream.bits(4)
        self.unk3 = stream.bits(4)
        self.font_region = stream.bits(4)
        self.region_move = stream.bits(2)
        self.unk4 = stream.bit()
        self.copyable = bool(stream.bit())
        self.mii_version = stream.u8()
        
        self.author_id = stream.repeat(stream.u8, 8)
        self.mii_id = stream.repeat(stream.u8, 10)
        self.unk5 = stream.read(2)
        
        self.unk6 = stream.bit()
        self.unk7 = stream.bit()
        self.color = stream.bits(4)
        self.birth_day = stream.bits(5)
        self.birth_month = stream.bits(4)
        self.gender = stream.bit()
        
        self.mii_name = stream.wchars(10).split("\0")[0]
        self.size = stream.u8()
        self.fatness = stream.u8()

        self.blush_type = stream.bits(4)
        self.face_style = stream.bits(4)
        self.face_color = stream.bits(3)
        self.face_type = stream.bits(4)
        self.local_only = bool(stream.bit())
        self.hair_mirrored = bool(stream.bits(5))
        self.hair_color = stream.bits(3)
        self.hair_type = stream.u8()
        
        self.eye_thickness = stream.bits(3)
        self.eye_scale = stream.bits(4)
        self.eye_color = stream.bits(3)
        self.eye_type = stream.bits(6)
        self.eye_height = stream.bits(7)
        self.eye_distance = stream.bits(4)
        self.eye_rotation = stream.bits(5)
        
        self.eyebrow_thickness = stream.bits(4)
        self.eyebrow_scale = stream.bits(4)
        self.eyebrow_color = stream.bits(3)
        self.eyebrow_type = stream.bits(5)
        self.eyebrow_height = stream.bits(7)
        self.eyebrow_distance = stream.bits(4)
        self.eyebrow_rotation = stream.bits(5)
        
        self.nose_height = stream.bits(7)
        self.nose_scale = stream.bits(4)
        self.nose_type = stream.bits(5)
        self.mouth_thickness = stream.bits(3)
        self.mouth_scale = stream.bits(4)
        self.mouth_color = stream.bits(3)
        self.mouth_type = stream.bits(6)

        self.unk34 = stream.u8()
        self.mustache_type = stream.bits(3)
        self.mouth_height = stream.bits(5)
        self.mustache_height = stream.bits(6)
        self.mustache_scale = stream.bits(4)
        self.beard_color = stream.bits(3)
        self.beard_type = stream.bits(3)
        
        self.glass_height = stream.bits(5)
        self.glass_scale = stream.bits(4)
        self.glass_color = stream.bits(3)
        self.glass_type = stream.bits(4)
        self.unk43 = stream.bit()
        self.mole_ypos = stream.bits(5)
        self.mole_xpos = stream.bits(5)
        self.mole_scale = stream.bits(4)
        self.mole_enabled = stream.bit()
        
        self.creator_name = stream.wchars(10).split("\0")[0]
        
        self.unk48 = stream.read(2)
        stream.u16()
        
        return self
        
    def encode(self, outstream):
        stream = streams.BitStreamOut(">")
        stream.bits(self.birth_platform, 4)
        stream.bits(self.unk1, 4)
        stream.bits(self.unk2, 4)
        stream.bits(self.unk3, 4)
        stream.bits(self.font_region, 4)
        stream.bits(self.region_move, 2)
        stream.bit(self.unk4)
        stream.bit(self.copyable)
        stream.u8(self.mii_version)
        stream.repeat(self.author_id, stream.u8)
        stream.repeat(self.mii_id, stream.u8)
        stream.write(self.unk5)
        stream.bit(self.unk6)
        stream.bit(self.unk7)
        stream.bits(self.color, 4)
        stream.bits(self.birth_day, 5)
        stream.bits(self.birth_month, 4)
        stream.bit(self.gender)
        stream.wchars(self.mii_name + "\0" * (10 - len(self.mii_name)))
        stream.u8(self.size)
        stream.u8(self.fatness)
        stream.bits(self.blush_type, 4)
        stream.bits(self.face_style, 4)
        stream.bits(self.face_color, 3)
        stream.bits(self.face_type, 4)
        stream.bit(self.local_only)
        stream.bits(self.hair_mirrored, 5)
        stream.bits(self.hair_color, 3)
        stream.u8(self.hair_type)
        stream.bits(self.eye_thickness, 3)
        stream.bits(self.eye_scale, 4)
        stream.bits(self.eye_color, 3)
        stream.bits(self.eye_type, 6)
        stream.bits(self.eye_height, 7)
        stream.bits(self.eye_distance, 4)
        stream.bits(self.eye_rotation, 5)
        stream.bits(self.eyebrow_thickness, 4)
        stream.bits(self.eyebrow_scale, 4)
        stream.bits(self.eyebrow_color, 3)
        stream.bits(self.eyebrow_type, 5)
        stream.bits(self.eyebrow_height, 7)
        stream.bits(self.eyebrow_distance, 4)
        stream.bits(self.eyebrow_rotation, 5)
        stream.bits(self.nose_height, 7)
        stream.bits(self.nose_scale, 4)
        stream.bits(self.nose_type, 5)
        stream.bits(self.mouth_thickness, 3)
        stream.bits(self.mouth_scale, 4)
        stream.bits(self.mouth_color, 3)
        stream.bits(self.mouth_type, 6)
        stream.u8(self.unk34)
        stream.bits(self.mustache_type, 3)
        stream.bits(self.mouth_height, 5)
        stream.bits(self.mustache_height, 6)
        stream.bits(self.mustache_scale, 4)
        stream.bits(self.beard_color, 3)
        stream.bits(self.beard_type, 3)
        stream.bits(self.glass_height, 5)
        stream.bits(self.glass_scale, 4)
        stream.bits(self.glass_color, 3)
        stream.bits(self.glass_type, 4)
        stream.bit(self.unk43)
        stream.bits(self.mole_ypos, 5)
        stream.bits(self.mole_xpos, 5)
        stream.bits(self.mole_scale, 4)
        stream.bit(self.mole_enabled)
        stream.wchars(self.creator_name + "\0" * (10 - len(self.creator_name)))
        stream.write(self.unk48)
        data = self.swap_endian(stream.get())
        outstream.write(data)
        outstream.u16(util.crc16(data + b"\0\0"))
        
    def swap_endian(self, data):
        array = bytearray(data)
        swap32(array, 0)
        for i in range(0x18, 0x2E, 2): swap16(array, i)
        for i in range(0x30, 0x48, 2): swap16(array, i)
        for i in range(0x48, 0x5C, 2): swap16(array, i)
        swap16(array, 0x5C)
        return bytes(array)
        
    def build(self):
        stream = streams.StreamOut(">")
        self.encode(stream)
        return stream.get()
    
    @classmethod
    def parse(cls, data):
        instance = cls()
        if instance.decode(streams.StreamIn(data, ">")) is None:
            return None
        return instance