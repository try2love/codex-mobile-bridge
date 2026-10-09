'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {interfaces}=require('../desktop/features/connections/network.cjs');
test('IPv4 adapter choices keep virtual adapter names and exclude loopback and IPv6',()=>{
  const rows=interfaces({Ethernet:[{address:'192.168.1.7',family:'IPv4',internal:false}],
    'vEthernet (WSL)':[ {address:'172.20.0.1',family:'IPv4',internal:false}],
    VMnet8:[{address:'192.168.55.1',family:'IPv4',internal:false}],
    lo:[{address:'127.0.0.1',family:'IPv4',internal:true},{address:'::1',family:'IPv6',internal:true}],
    IPv6:[{address:'fe80::1',family:'IPv6',internal:false}]});
  assert.equal(rows.length,3);assert.ok(rows.some(row=>row.name==='vEthernet (WSL)'&&row.address==='172.20.0.1'));
  assert.ok(rows.some(row=>row.name==='VMnet8'));
});
