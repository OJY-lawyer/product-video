import {copyFileSync, mkdirSync, readdirSync, realpathSync, statSync} from 'node:fs';
import path from 'node:path';

/** Materialize directory links using ordinary file copies on Windows/POSIX.
 * Node's native recursive cp with dereference can abort on Windows junctions.
 * Keep traversal in JavaScript and reject link cycles rather than recursing.
 */
export function copyTree(source, destination, ancestors = new Set()) {
  const stat = statSync(source);
  if (stat.isDirectory()) {
    const real = realpathSync(source);
    if (ancestors.has(real)) throw new Error('Circular link in project public assets.');
    const next = new Set(ancestors).add(real);
    mkdirSync(destination, {recursive:true});
    for (const name of readdirSync(source)) copyTree(path.join(source, name), path.join(destination, name), next);
  } else if (stat.isFile()) {
    mkdirSync(path.dirname(destination), {recursive:true});
    copyFileSync(source, destination);
  } else throw new Error('Unsupported special file in project public assets.');
}
