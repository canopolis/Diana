import struct, os
def u16(b,o): return struct.unpack_from('<H',b,o)[0]
def read_table(path):
    d=open(path,'rb').read()
    fields=[]; o=512
    while o+768<=len(d):
        n=d[o+2]
        name=d[o+3:o+3+n]
        if n==0 or n>60 or not name.replace(b'_',b'').isalnum(): break
        b=d[o:o+768]
        fields.append(dict(name=name.decode(),type=b[164],size=u16(b,166),phys=u16(b,169),off=u16(b,172)))
        o+=768
    return d,fields,o
import datetime
RS={'GLAccnt':112,'JV':168,'JVDet':64,'GLHist':192,'GLActual':168,'GLAcSub1':56,'GLAcSub2':56,'GLAcSub3':56,'JVCtl':40}
def rows(path,rs=None):
    if rs is None: rs=RS.get(os.path.splitext(os.path.basename(path))[0])
    d,f,o=read_table(path)
    if rs is None:
        end=f[-1]['off']+f[-1]['phys']
        rs=RS.get(os.path.splitext(os.path.basename(path))[0])
        if rs is None:
            rs=next(r for r in range(end+1,end+64) if (len(d)-o)%r==0)
    out=[]
    for p in range(o,len(d),rs):
        r=d[p:p+rs]
        rec={'_st':r[0],'_hdr':r[:24].hex()}
        for x in f:
            fo=x['off']; flag=r[fo]; v=r[fo+1:fo+1+x['phys']]
            if not flag: rec[x['name']]=None; continue
            t=x['type']
            if t==1: rec[x['name']]=v.split(b'\0')[0].decode('latin-1')
            elif t==6: rec[x['name']]=struct.unpack('<i',v)[0]
            elif t==5: rec[x['name']]=struct.unpack('<h',v)[0]
            elif t==4: rec[x['name']]=bool(v[0])
            elif t==7: rec[x['name']]=struct.unpack('<d',v)[0]
            elif t==2:
                n=struct.unpack('<i',v)[0]; rec[x['name']]=datetime.date(1,1,1)+datetime.timedelta(days=n-1)
            else: rec[x['name']]=v.hex()
        out.append(rec)
    return out
