//! A flipper for PortalDot, written directly against pallet-contracts 3.0.0.
//!
//! No ink!. No cargo-contract. No dependencies at all.
//!
//! The chain does not know what ink! is — `contracts.instantiate_with_code`
//! takes raw WASM bytes and nothing else. Contract metadata (the ABI) is a
//! client-side convenience for @polkadot/api-contract, never sent on-chain.
//! So the shortest path to the first contract on PortalDot is to skip the
//! whole ink! toolchain and speak to the host functions ourselves.
//!
//! Host functions come from frame/contracts/src/wasm/runtime.rs (`define_env!`),
//! imported from module "seal0".
//!
//! Exports: exactly `deploy` and `call`, both `() -> ()`. The chain rejects any
//! other export outright — see prepare.rs::scan_exports.

#![no_std]
#![allow(unused_unsafe)]

use core::panic::PanicInfo;

#[panic_handler]
fn panic(_info: &PanicInfo) -> ! {
    // `unreachable` traps, which the pallet surfaces as a contract trap.
    // Never allocate or format here: no_std, and no allocator is present.
    unsafe { core::arch::wasm32::unreachable() }
}

#[link(wasm_import_module = "seal0")]
extern "C" {
    fn seal_return(flags: u32, data_ptr: *const u8, data_len: u32) -> !;
    fn seal_set_storage(key_ptr: *const u8, value_ptr: *const u8, value_len: u32);
    fn seal_get_storage(key_ptr: *const u8, out_ptr: *mut u8, out_len_ptr: *mut u32) -> u32;
}

/// Storage keys in pallet-contracts 3.0.0 are fixed 32-byte keys.
const KEY_VALUE: [u8; 32] = [0u8; 32];

/// `seal_get_storage` returns ReturnCode::Success == 0.
const RETURN_CODE_SUCCESS: u32 = 0;

/// Constructor. Runs once, at instantiate. Seeds the stored flag to `false`.
#[no_mangle]
pub extern "C" fn deploy() {
    unsafe {
        let initial: [u8; 1] = [0u8];
        seal_set_storage(KEY_VALUE.as_ptr(), initial.as_ptr(), 1);
        seal_return(0, initial.as_ptr(), 0);
    }
}

/// Message. Flips the stored flag and returns the new value as one SCALE-encoded
/// bool (which is simply 0x00 or 0x01).
#[no_mangle]
pub extern "C" fn call() {
    unsafe {
        let mut buf: [u8; 1] = [0u8];
        let mut len: u32 = 1;

        let rc = seal_get_storage(
            KEY_VALUE.as_ptr(),
            buf.as_mut_ptr(),
            &mut len as *mut u32,
        );

        let current = if rc == RETURN_CODE_SUCCESS && len >= 1 { buf[0] } else { 0 };
        let next: [u8; 1] = [if current == 0 { 1u8 } else { 0u8 }];

        seal_set_storage(KEY_VALUE.as_ptr(), next.as_ptr(), 1);
        seal_return(0, next.as_ptr(), 1);
    }
}
