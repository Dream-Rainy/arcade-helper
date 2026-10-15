"use strict";
window.aliasRelay = new class {
  constructor(){
    this.pending=new Map();
    this.sent=this.received=0;
    this.closed=false;
    this.sendQueue=this.receiveQueue=Promise.resolve();
    this.ready=new Promise((resolve,reject)=>{this.resolveReady=resolve;this.rejectReady=reject;});
    this.ready.catch(()=>{});
    this.connect().catch(error=>this.fail(error));
  }
  bytes(text){return Uint8Array.from(atob(text),c=>c.charCodeAt(0));}
  base64(bytes){return btoa(Array.from(bytes,c=>String.fromCharCode(c)).join(""));}
  nonce(prefix,seq){const bytes=new Uint8Array(12);bytes.set(new TextEncoder().encode(prefix));new DataView(bytes.buffer).setBigUint64(4,BigInt(seq));return bytes;}
  async connect(){
    if(!window.isSecureContext||!crypto.subtle)throw new Error("编辑器需要 HTTPS；本机调试可使用 localhost。");
    const params=new URLSearchParams(location.hash.slice(1));
    this.channel=params.get("channel");
    history.replaceState(null,"",location.pathname);
    const access=params.get("key"),server=params.get("public"),secret=params.get("secret");
    if(!this.channel||!/^[A-Za-z0-9_-]{16,64}$/.test(this.channel)||!access||!server||!secret||!/^[A-Za-z0-9_-]{43}$/.test(secret))throw new Error("请私聊机器人发送 /arcade.editor，并打开机器人返回的新链接。");
    this.aad=new TextEncoder().encode(this.channel);
    const pair=await crypto.subtle.generateKey({name:"ECDH",namedCurve:"P-256"},false,["deriveBits"]);
    const peer=await crypto.subtle.importKey("raw",this.bytes(server),{name:"ECDH",namedCurve:"P-256"},false,[]);
    const shared=await crypto.subtle.deriveBits({name:"ECDH",public:peer},pair.privateKey,256);
    const material=await crypto.subtle.importKey("raw",shared,"HKDF",false,["deriveKey"]);
    this.key=await crypto.subtle.deriveKey({name:"HKDF",hash:"SHA-256",salt:this.aad,info:new TextEncoder().encode("arcade-alias-editor-v1")},material,{name:"AES-GCM",length:256},false,["encrypt","decrypt"]);
    const publicKey=this.base64(new Uint8Array(await crypto.subtle.exportKey("raw",pair.publicKey)));
    const authKey=await crypto.subtle.importKey("raw",new TextEncoder().encode(secret),{name:"HMAC",hash:"SHA-256"},false,["sign"]);
    const signature=new Uint8Array(await crypto.subtle.sign("HMAC",authKey,new TextEncoder().encode(`${this.channel}:${publicKey}`)));
    const proof=Array.from(signature,byte=>byte.toString(16).padStart(2,"0")).join("");
    const url=new URL(`./api/channels/${this.channel}/browser`,location.href);
    url.protocol=url.protocol==="https:"?"wss:":"ws:";
    this.socket=new WebSocket(url);
    this.timer=setTimeout(()=>this.fail(new Error("连接超时，请重新私聊机器人获取编辑链接。")),15000);
    this.socket.onopen=()=>this.socket.send(JSON.stringify({key:access}));
    this.socket.onmessage=event=>{
      this.receiveQueue=this.receiveQueue.then(()=>this.receive(event.data,publicKey,proof)).catch(error=>this.fail(error));
    };
    this.socket.onerror=()=>this.fail(new Error("中继连接失败，请检查网络或重新创建会话。"));
    this.socket.onclose=()=>this.fail(new Error("编辑会话已结束。若刚提交了操作，请新建会话后检查结果，避免重复提交。"));
  }
  async receive(raw,publicKey,proof){
    if(raw.length>1000000)throw new Error("中继消息过大。");
    const packet=JSON.parse(raw);
    if(packet.control==="connected"){
      this.socket.send(JSON.stringify({type:"hello",public:publicKey,proof}));
      return;
    }
    if(!Number.isSafeInteger(packet.seq)||packet.seq!==this.received+1||typeof packet.data!=="string")throw new Error("中继消息校验失败，请重新创建会话。");
    const decrypted=await crypto.subtle.decrypt({name:"AES-GCM",iv:this.nonce("SRVR",packet.seq),additionalData:this.aad},this.key,this.bytes(packet.data));
    this.received=packet.seq;
    const data=JSON.parse(new TextDecoder().decode(decrypted));
    if(data.type==="ready"){
      clearTimeout(this.timer);
      this.resolveReady();
    }else if(data.type==="response"){
      const request=this.pending.get(data.id);
      if(request){this.pending.delete(data.id);clearTimeout(request.timer);request.resolve({ok:data.status>=200&&data.status<300,status:data.status,json:async()=>data.data});}
    }else throw new Error("编辑会话消息格式错误。");
  }
  async request(path,body){
    await this.ready;
    if(this.closed)throw new Error("会话已结束，请私聊机器人重新创建。");
    if(this.pending.size>=8)throw new Error("操作较多，请等待当前操作完成。");
    const id=crypto.randomUUID();
    return new Promise((resolve,reject)=>{
      const timer=setTimeout(()=>{this.pending.delete(id);reject(new Error("响应超时；操作可能已生效，请刷新查看结果。"));},45000);
      this.pending.set(id,{resolve,reject,timer});
      this.sendQueue=this.sendQueue.then(async()=>{
        const seq=++this.sent;
        const raw=new TextEncoder().encode(JSON.stringify({type:"request",id,path,method:body===undefined?"GET":"POST",body:body??null}));
        if(raw.length>65536)throw new Error("提交内容过长。");
        const ciphertext=await crypto.subtle.encrypt({name:"AES-GCM",iv:this.nonce("CLNT",seq),additionalData:this.aad},this.key,raw);
        this.socket.send(JSON.stringify({seq,data:this.base64(new Uint8Array(ciphertext))}));
      }).catch(error=>this.fail(error));
    });
  }
  fail(error){
    if(this.closed)return;
    this.closed=true;
    clearTimeout(this.timer);
    this.rejectReady(error);
    for(const request of this.pending.values()){clearTimeout(request.timer);request.reject(error);}
    this.pending.clear();
    this.key=null;
    if(this.socket&&this.socket.readyState<2)this.socket.close();
    if(typeof signedOut==="function")signedOut();
    const node=document.getElementById("notice");node.textContent=error.message;node.className="error";node.hidden=false;
  }
}();
