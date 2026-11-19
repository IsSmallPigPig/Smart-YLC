"""手机上传页面（局域网直传用）。

内嵌的 HTML 不引任何外部资源：手机可能连不上外网，只跟电脑在同一个 Wi-Fi 里。
页面只做三件事：选 PDF、逐个上传并显示进度、把结果标出来。
"""

from __future__ import annotations

UPLOAD_PAGE = """<!doctype html>
<html lang="zh-CN"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>传到电脑</title>
<style>
:root{color-scheme:light dark}
*{box-sizing:border-box}
body{margin:0;padding:24px 18px 40px;font:16px/1.6 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif;background:#f5f6f8;color:#1a1a1a}
h1{font-size:19px;margin:0 0 6px}
p.sub{margin:0 0 20px;color:#6b7280;font-size:13px}
label.pick{display:block;padding:26px 18px;border:1.5px dashed #c3c7cf;border-radius:14px;text-align:center;background:#fff;color:#2563eb;font-weight:600}
label.pick input{display:none}
ul{list-style:none;padding:0;margin:16px 0 0}
li{background:#fff;border-radius:12px;padding:12px 14px;margin-bottom:8px;font-size:14px}
li .n{font-weight:600;word-break:break-all}
li .s{color:#6b7280;font-size:12px;margin-top:4px}
li.ok .s{color:#15803d}
li.bad .s{color:#b91c1c}
button{width:100%;margin-top:14px;padding:14px;border:0;border-radius:12px;background:#2563eb;color:#fff;font-size:16px;font-weight:600}
button[disabled]{background:#9ca3af}
.foot{margin-top:24px;color:#9ca3af;font-size:12px;text-align:center}
@media (prefers-color-scheme:dark){
body{background:#111318;color:#eceff4}
label.pick,li{background:#1b1e25;border-color:#333842}
h1{color:#eceff4}
}
</style></head><body>
<h1>传到电脑</h1>
<p class="sub">选择入团志愿书 PDF，文件名保持「姓名+手机号+入团志愿书.pdf」。</p>
<label class="pick">选择 PDF 文件<input id="f" type="file" accept="application/pdf,.pdf" multiple></label>
<ul id="list"></ul>
<button id="go" disabled>开始传输</button>
<div class="foot">只在这个 Wi-Fi 内传输，不经过互联网</div>
<script>
var files=[],pin=new URLSearchParams(location.search).get('k')||'';
var list=document.getElementById('list'),go=document.getElementById('go');
document.getElementById('f').addEventListener('change',function(e){
  files=Array.prototype.slice.call(e.target.files||[]);
  list.innerHTML='';
  files.forEach(function(f){
    var li=document.createElement('li');
    li.innerHTML='<div class="n"></div><div class="s">等待传输</div>';
    li.querySelector('.n').textContent=f.name;
    list.appendChild(li);
  });
  go.disabled=!files.length;
});
go.addEventListener('click',function(){
  if(!files.length)return;
  go.disabled=true;
  var i=0;
  function next(){
    if(i>=files.length){go.textContent='传输完成';return;}
    var f=files[i],li=list.children[i],s=li.querySelector('.s');
    s.textContent='传输中…';
    var xhr=new XMLHttpRequest();
    xhr.open('POST','/upload?k='+encodeURIComponent(pin)+'&name='+encodeURIComponent(f.name));
    xhr.setRequestHeader('Content-Type','application/pdf');
    xhr.upload.onprogress=function(e){
      if(e.lengthComputable){s.textContent='传输中 '+Math.round(e.loaded/e.total*100)+'%';}
    };
    xhr.onload=function(){
      var r={};try{r=JSON.parse(xhr.responseText);}catch(err){}
      var ok=xhr.status===200&&r.ok;
      li.className=ok?'ok':'bad';
      s.textContent=r.note||(ok?'已保存':'失败（'+xhr.status+'）');
      i++;next();
    };
    xhr.onerror=function(){li.className='bad';s.textContent='连接中断，检查是否还在同一个 Wi-Fi';i++;next();};
    xhr.send(f);
  }
  next();
});
</script>
</body></html>
"""
