// Negative cyclic NTT over auxiliary primes. Layout is [column][row][coefficient].
#include <algorithm>
#include <array>
#include <cstdint>
#include <vector>
using u32 = uint32_t;
using u64 = uint64_t;
constexpr int D = 8192, K = 4;
#define LANES(X) X(0,1073872897,7) X(1,1073971201,11) X(2,1074266113,5) X(3,1074282497,3) X(4,1074429953,3) X(5,1074610177,5) X(6,1075003393,11) X(7,1075363841,6) X(8,1075560449,3) X(9,1075937281,7) X(10,1076002817,3) X(11,1076576257,30) X(12,1076789249,3) X(13,1076920321,17) X(14,1077035009,3)
#define LIST_PRIME(i,p,g) p,
constexpr u32 primes[] = {LANES(LIST_PRIME)};
extern "C" u32 aux_prime(int i) { return i>=0 && i<int(sizeof(primes)/sizeof(primes[0])) ? primes[i] : 0; }

template<u32 P> static inline u32 mul(u32 x, u32 y) {return u64(x)*y%P;}
template<u32 P> static u32 power(u32 x,u32 e) {
    u32 result=1;
    while(e) { if(e&1) result=mul<P>(result,x); x=mul<P>(x,x); e>>=1; }
    return result;
}

template<u32 P,u32 G> struct Ntt {
    std::array<u32,D> twist{}, untwist{}, roots{}, invroots{};
    std::array<int,D> rev{};
    Ntt() {
        static_assert((P-1)%(2*D)==0);
        u32 psi=power<P>(G,(P-1)/(2*D)), inv=power<P>(psi,P-2);
        twist[0]=1; untwist[0]=power<P>(D,P-2);
        for(int i=1;i<D;++i) {
            rev[i]=(rev[i>>1]>>1)|((i&1)*(D>>1));
            twist[i]=mul<P>(twist[i-1],psi); untwist[i]=mul<P>(untwist[i-1],inv);
        }
        for(int h=1;h<D;h*=2) {
            roots[h]=1; invroots[h]=1;
            u32 step=power<P>(psi,D/h), back=power<P>(inv,D/h);
            for(int i=1;i<h;++i) {
                roots[h+i]=mul<P>(roots[h+i-1],step);
                invroots[h+i]=mul<P>(invroots[h+i-1],back);
            }
        }
    }
    void run(u32* v,bool inverse) const {
        const auto& ws=inverse?invroots:roots;
        if(!inverse) for(int i=0;i<D;++i) v[i]=mul<P>(v[i],twist[i]);
        for(int i=0;i<D;++i) if(i<rev[i]) std::swap(v[i],v[rev[i]]);
        for(int h=1;h<D;h*=2) for(int base=0;base<D;base+=2*h) {
            for(int j=0;j<h;++j) {
                u32 x=v[base+j], y=mul<P>(v[base+j+h],ws[h+j]);
                u32 sum=x+y;
                v[base+j]=sum>=P?sum-P:sum;
                v[base+j+h]=x>=y?x-y:x+P-y;
            }
        }
        if(inverse) for(int i=0;i<D;++i) v[i]=mul<P>(v[i],untwist[i]);
    }
};
template<u32 P,u32 G> const Ntt<P,G>& ntt() { static const Ntt<P,G> plan; return plan; }
template<u32 P> static inline u32 reduce(const u32* w,int words) {
    u32 rem=0;
    for(int i=words-1;i>=0;--i) rem=((u64(rem)<<32)+w[i])%P;
    return rem;
}
template<u32 P,u32 G> static void prepare(const u32* public_coeffs,u32* dst) {
    for(int row=0;row<K;++row) {
        for(int j=0;j<D;++j) dst[j]=reduce<P>(public_coeffs+6*j,6);
        ntt<P,G>().run(dst,false);
        public_coeffs+=6*D; dst+=D;
    }
}
template<u32 P,u32 G> static void evaluate(int cols,int format,const void* values,const u32* spectra,u32* out) {
    std::vector<u32> x(D);
    std::fill(out,out+K*D,0);
    for(int col=0;col<cols;++col) {
        const size_t start=size_t(col)*D;
        for(int i=0;i<D;++i) {
            if(format>0) x[i]=reduce<P>(static_cast<const u32*>(values)+(start+i)*format,format);
            else if(format==-4) {
                int64_t z=static_cast<const int32_t*>(values)[start+i];
                z%=int64_t(P);
                x[i]=static_cast<u32>(z<0?z+P:z);
            } else {
                int z=format==-1?static_cast<const int8_t*>(values)[start+i]:static_cast<const int16_t*>(values)[start+i];
                x[i]=z<0?P+z:z;
            }
        }
        ntt<P,G>().run(x.data(),false);
        for(int row=0;row<K;++row) {
            u32* accum=out+row*D;
            for(int i=0;i<D;++i) {
                u32 sum=accum[i]+mul<P>(spectra[i],x[i]);
                accum[i]=sum>=P?sum-P:sum;
            }
            spectra+=D;
        }
    }
    for(int row=0;row<K;++row) ntt<P,G>().run(out+row*D,true);
}
#define PREP(i,p,g) case i: prepare<p,g>(src,dst);return 1;
extern "C" int prepare_lane(int lane,const u32* src,u32* dst) {
    switch(lane) {LANES(PREP) default:return 0;}
}
#define EVAL(i,p,g) case i: evaluate<p,g>(cols,format,values,spectra,out);return 1;
extern "C" int evaluate_lane(int lane,int cols,int format,const void* values,const u32* spectra,u32* out) {
    if(cols<=0 || !(format==-1 || format==-2 || format==-4 || format==6)) return 0;
    switch(lane) {LANES(EVAL) default:return 0;}
}
