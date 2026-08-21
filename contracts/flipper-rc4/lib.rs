//! The same flipper, written in real ink! this time.
//!
//! `contracts/minimal` proved the chain accepts contracts by skipping ink!
//! entirely. That is useful, but nobody wants to hand-write `seal0` calls. This
//! is the version that matters for everyone else: if ink! source builds into a
//! module PortalDot accepts, then the four projects currently stuck on ink! 4/5
//! can move by changing versions rather than rewriting.
//!
//! PortalDot runs pallet-contracts 3.0.0, whose host functions live in module
//! `seal0`, so this pins the ink! 3.x line. `tools/portawasm.py check` will name
//! any import the runtime does not define.

#![cfg_attr(not(feature = "std"), no_std)]

use ink_lang as ink;

#[ink::contract]
mod flipper {

    #[ink(storage)]
    pub struct Flipper {
        value: bool,
    }

    impl Flipper {
        /// Construct with an explicit initial value.
        #[ink(constructor)]
        pub fn new(init_value: bool) -> Self {
            Self { value: init_value }
        }

        /// Construct with `false`.
        #[ink(constructor)]
        pub fn default() -> Self {
            Self::new(false)
        }

        /// Flip the stored value.
        #[ink(message)]
        pub fn flip(&mut self) {
            self.value = !self.value;
        }

        /// Read the stored value.
        #[ink(message)]
        pub fn get(&self) -> bool {
            self.value
        }
    }

    #[cfg(test)]
    mod tests {
        use super::*;
        use ink_lang as ink;

        #[ink::test]
        fn default_works() {
            let flipper = Flipper::default();
            assert_eq!(flipper.get(), false);
        }

        #[ink::test]
        fn it_flips() {
            let mut flipper = Flipper::new(false);
            flipper.flip();
            assert_eq!(flipper.get(), true);
        }
    }
}
