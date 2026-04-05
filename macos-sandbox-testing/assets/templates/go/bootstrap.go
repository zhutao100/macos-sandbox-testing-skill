//go:build darwin

package msstbootstrap

// This package exists solely to ensure SandboxTestingBootstrap.c is compiled and linked
// into Go test binaries.
//
// The actual sandbox/tripwire is applied by the Mach-O constructor in SandboxTestingBootstrap.c.

/*
#cgo CFLAGS: -std=c11
#cgo LDFLAGS: -lsandbox
*/
import "C"
