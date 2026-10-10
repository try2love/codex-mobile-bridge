'use strict';
const QRCode=require('qrcode');
// Local rendering only. Do not send the bearer URL to a QR image service.
async function pairingImage(grant){
  const image=await QRCode.toDataURL(grant.url,{type:'image/png',errorCorrectionLevel:'M',margin:4,width:320});
  const {url,...status}=grant;
  return {...status,image};
}
module.exports={pairingImage};
