const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../static/app.js'), 'utf8');
const validator = source.slice(source.indexOf('function validateLayout('), source.indexOf('function readSaved('));
const context = vm.createContext({});
vm.runInContext(validator, context);
const valid = Array.from({length:150}, (_, i) => ({index:i+1,color:'#ff0000',brightness:50}));
assert.equal(context.validateLayout(valid), true);
for (const value of [null, [], valid.slice(1), valid.map((p,i)=>i===0?{...p,index:151}:p), valid.map((p,i)=>i===0?{...p,color:'red'}:p), valid.map((p,i)=>i===0?{...p,brightness:NaN}:p), valid.map((p,i)=>i===0?{...p,brightness:101}:p), valid.map((p,i)=>i===0?{...p,local_key:'secret'}:p)]) {
  assert.equal(context.validateLayout(value), false);
}
console.log('Saved-layout validation: 9 assertions passed.');
