// Preload entrypoint for Node/TypeScript projects.
//
// Load the native addon as early as possible so the bootstrap constructor runs
// before application/test code. This is best used via NODE_OPTIONS=--require.

const path = require('path');

// node-gyp default output
const addonPath = path.join(__dirname, '..', 'build', 'Release', 'msst_bootstrap.node');

// Loading the addon triggers the Mach-O constructor in SandboxTestingBootstrap.c.
require(addonPath);
