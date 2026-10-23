import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const source = fs.readFileSync(new URL("../web/app.js", import.meta.url), "utf8");
const start = source.indexOf("function classifyDictionaryValue(");
const end = source.indexOf("$(\"suggest-add\")", start);
assert.ok(start >= 0 && end > start, "classification function is present in the add flow");
const context = vm.createContext({});
vm.runInContext(`${source.slice(start, end)}; globalThis.classify = classifyDictionaryValue;`, context);
const classify = context.classify;

assert.deepEqual({ ...classify("person@example.com") }, { file: "emails", type: "EMAIL", ambiguous: false });
assert.deepEqual({ ...classify("+39 00 0000 0000") }, { file: "phone_numbers", type: "TELEFONO", ambiguous: false });
assert.deepEqual({ ...classify("Via Roma 12") }, { file: "addresses", type: "INDIRIZZO", ambiguous: false });
assert.equal(classify("non è un indirizzo").file, "entities");
assert.equal(classify("persona@example").ambiguous, true);
assert.equal(classify("123").ambiguous, true);
assert.equal(classify("+39 )02( 1234 5678").ambiguous, true);
assert.equal(classify("Via Roma").file, "entities");
assert.equal(classify("Via !!! 12").ambiguous, true);
assert.equal(classify("Via Roma 123456").ambiguous, true);
assert.equal(classify("person..name@example.test").ambiguous, true);
assert.equal(classify("name@example.c").ambiguous, true);
