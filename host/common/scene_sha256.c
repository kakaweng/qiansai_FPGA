/* Small file-only SHA256 utility for boards whose toybox omits sha256sum.
 * Uses only ISO C; it is run with the same glibc loader as the scene server. */
#include <stdint.h>
#include <stdio.h>
#include <string.h>

struct sha256 {
    uint32_t state[8];
    uint64_t bytes;
    unsigned used;
    unsigned char block[64];
};

static uint32_t rotate(uint32_t value, unsigned bits)
{
    return (value >> bits) | (value << (32 - bits));
}

static void compress(struct sha256 *hash, const unsigned char block[64])
{
    static const uint32_t constants[64] = {
        0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
        0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
        0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
        0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
        0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
        0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
        0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
        0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2
    };
    uint32_t words[64], a=hash->state[0], b=hash->state[1], c=hash->state[2], d=hash->state[3];
    uint32_t e=hash->state[4], f=hash->state[5], g=hash->state[6], h=hash->state[7];
    unsigned index;
    for (index=0; index<16; index++) {
        const unsigned char *p=block+index*4;
        words[index]=(uint32_t)p[0]<<24 | (uint32_t)p[1]<<16 | (uint32_t)p[2]<<8 | p[3];
    }
    for (; index<64; index++) {
        uint32_t x=words[index-15], y=words[index-2];
        words[index]=words[index-16]+(rotate(x,7)^rotate(x,18)^(x>>3))+
                     words[index-7]+(rotate(y,17)^rotate(y,19)^(y>>10));
    }
    for (index=0; index<64; index++) {
        uint32_t first=h+(rotate(e,6)^rotate(e,11)^rotate(e,25))+((e&f)^(~e&g))+constants[index]+words[index];
        uint32_t second=(rotate(a,2)^rotate(a,13)^rotate(a,22))+((a&b)^(a&c)^(b&c));
        h=g; g=f; f=e; e=d+first; d=c; c=b; b=a; a=first+second;
    }
    hash->state[0]+=a; hash->state[1]+=b; hash->state[2]+=c; hash->state[3]+=d;
    hash->state[4]+=e; hash->state[5]+=f; hash->state[6]+=g; hash->state[7]+=h;
}

static void update(struct sha256 *hash, const unsigned char *data, size_t size)
{
    hash->bytes+=(uint64_t)size;
    while (size) {
        size_t count=64-hash->used;
        if (count>size) count=size;
        memcpy(hash->block+hash->used,data,count);
        hash->used+=(unsigned)count; data+=count; size-=count;
        if (hash->used==64) { compress(hash,hash->block); hash->used=0; }
    }
}

static void finish(struct sha256 *hash)
{
    uint64_t bits=hash->bytes*8;
    unsigned index;
    hash->block[hash->used++]=0x80;
    if (hash->used>56) {
        memset(hash->block+hash->used,0,64-hash->used);
        compress(hash,hash->block); hash->used=0;
    }
    memset(hash->block+hash->used,0,56-hash->used);
    for (index=0; index<8; index++) hash->block[63-index]=(unsigned char)(bits>>(index*8));
    compress(hash,hash->block);
}

int main(int argc, char **argv)
{
    struct sha256 hash={{0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,
                         0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19},0,0,{0}};
    unsigned char buffer[16384];
    FILE *stream;
    size_t count;
    unsigned index;
    if (argc!=2) { fprintf(stderr,"Usage: scene_sha256 FILE\n"); return 2; }
    stream=fopen(argv[1],"rb");
    if (!stream) { perror(argv[1]); return 1; }
    while ((count=fread(buffer,1,sizeof(buffer),stream))!=0) update(&hash,buffer,count);
    if (ferror(stream)) { perror(argv[1]); fclose(stream); return 1; }
    if (fclose(stream)) { perror(argv[1]); return 1; }
    finish(&hash);
    for (index=0; index<8; index++) printf("%08lx",(unsigned long)hash.state[index]);
    return printf("  %s\n",argv[1])<0 ? 1 : 0;
}
