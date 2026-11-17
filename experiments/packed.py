"""Exact packed-message VOSA verification-layer arithmetic for the CPU pilot."""
import ctypes
import hashlib
import json
import math
import os
import platform
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal, ROUND_CEILING, localcontext
from pathlib import Path
from time import perf_counter
import numpy as np

ROOT=Path(__file__).resolve().parent
D=8192
K=4
L=10_000
Q=2**192-237
ETA=2**168
THETA=1
BOUND=6_000_000
PACK_CAPACITY=1000
# Same radix rule and public digit bound used by the original real-FL bridge.
BASE_BITS=(PACK_CAPACITY*(1<<31)).bit_length()
SLOTS=168//BASE_BITS
OCCUPIED=(L+SLOTS-1)//SLOTS
WORD_BYTES=24
CLOCK=perf_counter

def full_columns(n,theta=THETA):
    with localcontext() as ctx:
        ctx.prec=100
        return int(((K*n*Decimal(Q).ln()+Decimal(256)*Decimal(2).ln()/D)/
                    Decimal(2*theta+1).ln()).to_integral_value(rounding=ROUND_CEILING))

def find_compiler():
    c = shutil.which(os.getenv("CXX", "g++"))
    if c:
        return c
    for candidate in [
        r"C:\Users\JamesHarden\AppData\Local\Microsoft\WinGet\Packages\BrechtSanders.WinLibs.POSIX.UCRT_Microsoft.Winget.Source_8wekyb3d8bbwe\mingw64\bin\g++.exe",
        r"C:\msys64\mingw64\bin\g++.exe",
        r"C:\mingw64\bin\g++.exe",
        r"C:\Program Files\LLVM\bin\clang++.exe"
    ]:
        if os.path.exists(candidate):
            return candidate
    return None

def build():
    compiler=find_compiler()
    if not compiler:
        raise RuntimeError("C++17 compiler missing (g++ or set CXX)")
    src=ROOT/"packed_ntt.cpp"
    version=subprocess.check_output([compiler,"--version"],text=True).splitlines()[0]
    flags=["-O3","-std=c++17","-fPIC","-shared"]
    digest=hashlib.sha256(src.read_bytes()+repr((flags,version,platform.platform())).encode()).hexdigest()[:20]
    directory=Path(tempfile.gettempdir())/"vosa-packed-ntt-build"
    directory.mkdir(exist_ok=True)
    path=directory/(digest+(".dll" if os.name=="nt" else ".so"))
    if not path.exists():
        tmp=path.with_name(path.name+f".{os.getpid()}")
        try:
            subprocess.run([compiler,*flags,str(src),"-o",str(tmp)],check=True)
            os.replace(tmp,path)
        finally:
            tmp.unlink(missing_ok=True)
    if os.name=="nt":
        with os.add_dll_directory(str(Path(compiler).parent)):
            lib=ctypes.CDLL(str(path))
    else:
        lib=ctypes.CDLL(str(path))
    ptr=ctypes.c_void_p
    lib.aux_prime.argtypes=[ctypes.c_int]
    lib.aux_prime.restype=ctypes.c_uint32
    lib.prepare_lane.argtypes=[ctypes.c_int,ptr,ptr]
    lib.evaluate_lane.argtypes=[ctypes.c_int,ctypes.c_int,ctypes.c_int,ptr,ptr,ptr]
    return lib,dict(compiler=version,binary_sha256=hashlib.sha256(path.read_bytes()).hexdigest())

def pack_aggregate(signed_sums,count):
    if not 1<=count<=PACK_CAPACITY or len(signed_sums)!=L:
        raise ValueError("invalid sender count or length")
    nonnegative=[int(z)+count*BOUND for z in signed_sums]
    if any(z<0 or z>2*count*BOUND for z in nonnegative):
        raise ValueError("signed aggregate exceeds quantization range")
    vals=[sum(z<<(j*BASE_BITS) for j,z in enumerate(nonnegative[i:i+SLOTS]))
          for i in range(0,L,SLOTS)]
    return vals+[0]*(D-len(vals))

def unpack(packed,count):
    result=[]
    digit_mask=(1<<BASE_BITS)-1
    for coeff in packed[:OCCUPIED]:
        coeff=int(coeff)
        for _ in range(min(SLOTS,L-len(result))):
            result.append((coeff&digit_mask)-count*BOUND)
            coeff>>=BASE_BITS
        if coeff:
            raise ValueError("unexpected carry or nonzero padding")
    if any(packed[OCCUPIED:]):
        raise ValueError("nonzero tail")
    return result

def wide(values):
    return np.frombuffer(b"".join(int(x).to_bytes(WORD_BYTES,"little") for x in values),
                         dtype="<u4").reshape(1,D,6)

def encode_coordinates(values,count,eta):
    """Place each signed coordinate in one coefficient, preserving integer addition."""
    values=np.asarray(values)
    if (values.shape!=(L,) or not np.issubdtype(values.dtype,np.signedinteger)
            or not 1<=count<=PACK_CAPACITY or not 0<eta<=2**20):
        raise ValueError("invalid bounded coordinate input")
    bound=count*eta
    if int(values.min()) < -bound or int(values.max()) > bound:
        raise ValueError("quantized coordinate exceeds the selected message bound")
    out=np.zeros(((L+D-1)//D,D),dtype=np.int32)
    out.ravel()[:L]=values
    return out

def random_public():
    # Rejection from [0,2^192), shared A coefficients under all auxiliary primes.
    words=np.frombuffer(os.urandom(K*D*WORD_BYTES),dtype="<u4").copy().reshape(-1,6)
    bad=(words[:,0]>=2**32-237)&np.all(words[:,1:]==2**32-1,axis=1)
    while bad.any():
        words[bad]=np.frombuffer(os.urandom(int(bad.sum())*WORD_BYTES),dtype="<u4").reshape(-1,6)
        bad=(words[:,0]>=2**32-237)&np.all(words[:,1:]==2**32-1,axis=1)
    return words.reshape(K,D,6)

def coins(cols,theta=THETA):
    if theta!=1:
        if not 1<theta<=2**20:
            raise ValueError("unsupported randomness coefficient bound")
        # A multiple of the interval width gives every coefficient equal probability.
        width=2*theta+1
        last=(2**32//width)*width-1
        out=np.empty((cols,D),dtype=np.int32)
        for off in range(0,cols,32):
            batch=out[off:off+32]
            words=np.frombuffer(os.urandom(4*batch.size),dtype="<u4").copy()
            reject=words>last
            while reject.any():
                words[reject]=np.frombuffer(os.urandom(4*int(reject.sum())),dtype="<u4")
                reject=words>last
            batch[:]=(words%width).astype(np.int32).reshape(batch.shape)-theta
        return out
    out=np.empty((cols,D),dtype=np.int8)
    for off in range(0,cols,32):
        view=out[off:off+32]
        a=np.frombuffer(os.urandom(view.size),dtype=np.uint8).copy()
        bad=a==255
        while bad.any():
            a[bad]=np.frombuffer(os.urandom(int(bad.sum())),dtype=np.uint8)
            bad=a==255
        view[:]=(a%3).reshape(view.shape)
        view-=1
    return out

def bases(lib,cols,abs_bound):
    bound=2*D*cols*(Q-1)*abs_bound
    product=1
    primes=[]
    while product<=bound:
        p=lib.aux_prime(len(primes))
        if not p:
            raise ArithmeticError("not enough auxiliary moduli")
        primes.append(p)
        product*=p
    return primes

def coefficient_integers(words):
    return [int.from_bytes(w,"little") for w in words.reshape(-1,6).view("V24").ravel()]

class Matrix:
    def __init__(self,lib,cols,abs_bound,workers,keep=False,show=False):
        self.lib=lib
        self.cols=cols
        self.abs_bound=abs_bound
        self.primes=bases(lib,cols,abs_bound)
        self.data=np.empty((len(self.primes),cols,K,D),dtype="<u4")
        self.raw={} if keep else None
        self.modulus=math.prod(self.primes)
        # CRT with precomputed idempotents; the final reduction is per coefficient.
        self.weights=[]
        for p in self.primes:
            other=self.modulus//p
            self.weights.append(other*pow(other,-1,p))
        def prepare(c):
            values=random_public()
            if keep:
                self.raw[c]=values
            for lane in range(len(self.primes)):
                if not lib.prepare_lane(lane,values.ctypes.data,self.data[lane,c].ctypes.data):
                    raise ValueError("unknown auxiliary modulus")
            return hashlib.sha256(values).digest()
        digest=hashlib.sha256()
        with ThreadPoolExecutor(max_workers=workers) as pool:
            # Limit queued futures even for the manuscript-size column count.
            for start in range(0,cols,128):
                for tag in pool.map(prepare,range(start,min(start+128,cols))):
                    digest.update(tag)
                if show and (start%2048==0 or start+128>=cols):
                    print(f"PUBLIC columns={min(start+128,cols)}/{cols}",flush=True)
        self.digest=digest.hexdigest()

    def evaluate(self,x):
        if not x.flags.c_contiguous or x.shape[:2]!=(self.cols,D):
            raise ValueError("invalid input layout")
        if x.dtype==np.uint32 and x.shape==(self.cols,D,6):
            fmt=6
        elif x.dtype==np.int16 and x.ndim==2:
            fmt=-2
        elif x.dtype==np.int32 and x.ndim==2:
            fmt=-4
        elif x.dtype==np.int8 and x.ndim==2:
            fmt=-1
        else:
            raise ValueError("invalid coefficient representation")
        residues=np.empty((len(self.primes),K,D),dtype=np.uint32)
        for lane in range(len(self.primes)):
            if not self.lib.evaluate_lane(lane,self.cols,fmt,x.ctypes.data,
                                          self.data[lane].ctypes.data,residues[lane].ctypes.data):
                raise ValueError("native NTT failure")
        z=np.zeros((K,D),dtype=object)
        for row,w in zip(residues,self.weights):
            z+=row.astype(object)*w
        z%=self.modulus
        mask=z>self.modulus//2
        z[mask]-=self.modulus
        return z%Q

def commit(a1,a2,raw,theta=THETA,eta=ETA,direct=False):
    start=CLOCK()
    r=coins(a1.cols,theta)
    after_coins=CLOCK()
    m=encode_coordinates(raw,1,eta) if direct else wide(pack_aggregate(raw,1))
    after_pack=CLOCK()
    ar=a1.evaluate(r)
    after_a1=CLOCK()
    ax=a2.evaluate(m)
    after_a2=CLOCK()
    ct=(ar+ax)%Q
    return r,ct,dict(coins=after_coins-start,pack=after_pack-after_coins,
                     a1=after_a1-after_pack,a2=after_a2-after_a1,client=CLOCK()-start)

def aggregate_open(a1,a2,received,ids,n,r,signed_sum,theta=THETA,eta=ETA,direct=False):
    start=CLOCK()
    if len(ids)!=len(set(ids)) or set(ids)!=set(received) or len(ids)<4*n//5 or any(i<0 or i>=n for i in ids):
        return False,{}
    dtype=np.int16 if theta==1 else np.int32
    if r.shape!=(a1.cols,D) or r.dtype!=dtype or int(r.min()) < -n*theta or int(r.max())>n*theta:
        return False,{}
    try:
        if direct:
            v=encode_coordinates(signed_sum,len(ids),eta)
        else:
            packed=pack_aggregate(signed_sum,len(ids))
            if max(packed)>n*eta:
                return False,{}
            v=wide(packed)
    except ValueError:
        return False,{}
    total=np.zeros((K,D),dtype=object)
    for i in ids:
        total+=received[i]
    total%=Q
    t=CLOCK()
    ar=a1.evaluate(r)
    mid=CLOCK()
    ax=a2.evaluate(v)
    end=CLOCK()
    accepted=np.array_equal(total,(ar+ax)%Q)
    return accepted,dict(a1=mid-t,a2=end-mid,verifier=CLOCK()-start)
