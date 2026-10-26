#ifndef AQUASPACE_PROCESS_INVENTORY_H
#define AQUASPACE_PROCESS_INVENTORY_H
#include <stdint.h>
#include <stddef.h>
#include <sys/types.h>

// Read-only XNU exec-generation ABI. No private entitlement or
// elevated privilege is needed. Fail closed if unavailable on a host OS.
typedef struct {
    uint8_t uuid[16];
    uint64_t uniqueid;
    uint64_t parent_uniqueid;
    int32_t idversion;
    int32_t parent_version;
    uint64_t reserved[2];
} AquaSpaceProcUniqueInfo;
_Static_assert(sizeof(AquaSpaceProcUniqueInfo) == 56, "XNU unique-process ABI");

#endif
