// Removes the shared runtime's machine-local build state for temporary fixtures.
// Mirrors runtime/project_runtime.py state_base()/state_dir(): the passwd home's
// .cache/spring-boot-tools/build-<sha256 of the resolved project root>.
const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const stateBase = () => path.join(os.userInfo().homedir, '.cache', 'spring-boot-tools');
function stateDir(root) {
  const key = crypto.createHash('sha256').update(fs.realpathSync(root)).digest('hex');
  return path.join(stateBase(), `build-${key}`);
}
// Every directory below a fixture can be a discovered root; symlinks are not followed.
function directories(base) {
  const found = [base];
  for (const entry of fs.readdirSync(base, { withFileTypes: true })) {
    if (entry.isDirectory()) found.push(...directories(path.join(base, entry.name)));
  }
  return found;
}
exports.stateDir = stateDir;
exports.removeBuildState = base => {
  if (!fs.existsSync(base)) return;
  for (const directory of directories(base)) fs.rmSync(stateDir(directory), { recursive: true, force: true });
};
