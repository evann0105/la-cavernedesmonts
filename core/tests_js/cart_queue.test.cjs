const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

test('a removal is queued while a quantity request is in flight', async () => {
  const handlers = {};
  const requests = [];
  let scheduled;
  const input = {value:'2', checkValidity:()=>true, matches:()=>true};
  const main = {
    querySelector:selector => selector === '[data-cart-page]' ? {} : null,
    querySelectorAll:()=>[], childNodes:[], replaceChildren(){}, append(){},
    setAttribute(){}, removeAttribute(){},
    addEventListener:(name,callback)=>handlers[name]=callback,
  };
  const document = {getElementById:()=>main,documentElement:{lang:'fr'},
    createElement:()=>({setAttribute(){}}),querySelector:()=>null};
  const page = {getElementById:()=>main,querySelector:selector=>selector==='[data-cart-page]'?{}:null};
  class FormData { constructor(form){ this.quantity=form.elements.quantity.value; } }
  class DOMParser { parseFromString(){ return page; } }
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../static/core/js/cart.js'),'utf8'),{
    document,Intl,Map,Number,Math,Error,FormData,DOMParser,
    setTimeout:callback=>{scheduled=callback;return 1;}, clearTimeout(){},
    fetch:(url,options)=>new Promise(resolve=>requests.push({quantity:options.body.quantity,resolve})),
  });
  const form={action:'/panier/modifier/',elements:{quantity:input,key:{value:'variant:1'}}};
  input.form=form;
  handlers.input({target:input}); scheduled();
  assert.equal(requests.length,1);
  let prevented=false;
  const remove={action:form.action,elements:{quantity:{value:'0',checkValidity:()=>true},key:{value:'variant:1'}},
    matches:()=>true,closest:()=>({querySelector:()=>input})};
  handlers.submit({target:remove,preventDefault(){prevented=true;}});
  assert.equal(prevented,true);
  requests[0].resolve({ok:true,text:async()=>''});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(requests.length,2);
  assert.equal(requests[1].quantity,'0');
  requests[1].resolve({ok:true,text:async()=>''});
  await new Promise(resolve=>setImmediate(resolve));
});
