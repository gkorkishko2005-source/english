/* webapp/data/growth_engine.js
   PolyGlotty GrowthEngine
   -----------------------
   A self-contained procedural Canvas replacement for the legacy SVG
   growth tree. The engine owns one canvas, builds an organic branching
   model from deterministic seeded randomness, then animates leaves,
   fruit, weather particles and tap reactions in a single RAF loop.

   Architecture in one pass:
   - mount(container, state): creates a DPR-aware canvas and an instance.
   - Geometry is rebuilt on resize/stage changes. Stages 1-4 are hand
     botanical forms; stages 5-8 use an asymmetric recursive branching
     system with left/right seed variation.
   - Leaves and fruit are retained objects, not recreated every frame.
     Wind samples cheap value-noise, tap waves perturb nearby leaves,
     and fruit can detach, fall, puff, then regrow.
   - Weather uses pooled particles: fireflies, drifting leaves, wind
     streaks, rain drops and splashes.
   - replaceLegacyHook() removes #growth-svg/#growth-weather, mounts the
     engine into .growth-stage, and patches window.renderGrowth(d) so the
     rest of the app can keep calling the same function.
   No external dependencies, no build step. */
(function(){
  'use strict';

  var TWO=Math.PI*2, DPR=Math.min(2,window.devicePixelRatio||1);
  var MOOD={fresh:[.05,.18],breezy:[.12,.38],windy:[.32,.9],rainy:[.18,1.15],wilt:[.03,.07],radiant:[.06,.20]};
  var STAGE_NAMES=['Seedling','Sprout','Bud','Bloom','Bush','Sapling','Tree','Grand Tree'];

  function clamp(v,a,b){return v<a?a:v>b?b:v}
  function lerp(a,b,t){return a+(b-a)*t}
  function ease(t){return t*t*(3-2*t)}
  function hash(n){n=(n<<13)^n;return 1-((n*(n*n*15731+789221)+1376312589)&0x7fffffff)/1073741824}
  function noise(x,y,t){
    var xi=Math.floor(x),yi=Math.floor(y),xf=x-xi,yf=y-yi,u=ease(xf),v=ease(yf),s=Math.floor(t);
    var a=hash(xi*11+yi*101+s*997),b=hash((xi+1)*11+yi*101+s*997);
    var c=hash(xi*11+(yi+1)*101+s*997),d=hash((xi+1)*11+(yi+1)*101+s*997);
    return lerp(lerp(a,b,u),lerp(c,d,u),v);
  }
  function rng(seed){return function(){seed=(seed*1664525+1013904223)>>>0;return seed/4294967296}}
  function css(el,k,fb){return (getComputedStyle(el).getPropertyValue(k)||fb).trim()||fb}
  /* ── Stage colour ramp ─────────────────────────────────────────
     Progress should read as colour, not a flat blue blueprint. The
     foliage hue walks the life cycle:
       1 Seedling · 2 Sprout  → fresh green
       3 Bud                  → blue (a closed bud about to open)
       4 Bloom                → teal-green stem (petals stay pink/gold)
       5 Bush · 6 Sapling     → blue-green → blue
       7 Tree · 8 Grand Tree  → saturated green → vivid teal
     We interpolate between the anchors using the float stage so the
     colour drifts smoothly as XP climbs. */
  var STAGE_HEX=['#5fce72','#54c98a','#4f9bf0','#3fb89a','#34b0a8','#3aa0d8','#2fc55f','#1fd6a0'];
  function hx(h){h=String(h).replace('#','');if(h.length===3)h=h[0]+h[0]+h[1]+h[1]+h[2]+h[2];return[parseInt(h.slice(0,2),16),parseInt(h.slice(2,4),16),parseInt(h.slice(4,6),16)]}
  function mix(a,b,t){var x=hx(a),y=hx(b);return'rgb('+Math.round(lerp(x[0],y[0],t))+','+Math.round(lerp(x[1],y[1],t))+','+Math.round(lerp(x[2],y[2],t))+')'}
  function stageHue(sf){var s=clamp(Math.floor(sf),1,8),p=clamp(sf-s,0,1);return mix(STAGE_HEX[s-1],STAGE_HEX[Math.min(7,s)],p)}
  /* Build an rgba() string at alpha `a` from either a #hex or an
     rgb(r,g,b) colour, so tints (foliage, bark) can be drawn softly. */
  function rgba(col,a){if(!col)return'rgba(0,0,0,'+a+')';if(col.charAt(0)==='#'){var p=hx(col);return'rgba('+p[0]+','+p[1]+','+p[2]+','+a+')'}return col.indexOf('rgb(')===0?col.replace('rgb(','rgba(').replace(')',','+a+')'):col}

  function Engine(container,opt){
    opt=opt||{};this.c=container;this.stage=clamp(opt.stage||1,1,8);this.targetStage=this.stage;
    this.xp=opt.xp||0;this.maxXp=opt.maxXp||100;this.mood=opt.mood||'fresh';this.prevMood=this.mood;
    /* Foliage is tinted by the learner's CEFR level when provided. */
    this.levelCol=(typeof opt.levelCol==='string'&&opt.levelCol.charAt(0)==='#')?opt.levelCol:null;
    this.moodBlend=1;this.onTap=opt.onTap;this.rm=matchMedia('(prefers-reduced-motion: reduce)').matches;
    this.a=css(container,'--a','#4f8aff');this.t=css(container,'--t1','#eef2ff');
    /* Wood tone: a warm bark brown so a mature trunk reads as timber on
       the dark card instead of a stark white blob. Overridable via
       --tree-bark; falls back to a mid warm brown that sits well on dark. */
    this.bark=css(container,'--tree-bark','#8a6f57');
    /* Foliage colour (set per-frame in draw via stageHue) + a warm
       fruit tone so berries pop against green/teal leaves. */
    this.fol=stageHue(this.stage);this.fruitCol='#ffb454';
    this.cv=document.createElement('canvas');this.cv.setAttribute('role','img');this.cv.style.cssText='width:100%;height:100%;display:block;pointer-events:none';
    this.ctx=this.cv.getContext('2d');container.innerHTML='';container.appendChild(this.cv);
    this.w=this.h=1;this.base=1;this.br=[];this.leaves=[];this.fruit=[];this.fallen=[];this.puffs=[];this.parts=[];
    /* User interaction has been removed: no tap waves, no drag wind,
       no fruit drop on touch. The tree's job is to look beautiful
       and quietly mark progress, not to be a toy. */
    this.hidden=false;this.inView=true;this.slow=0;this.cap=1;this.last=performance.now();
    /* Progress shimmer: each setXp() with a higher value spawns a
       gentle light streak that travels up the trunk. */
    this.shimmers=[];this.lastXpSeen=opt.xp||0;
    this.resize=this.resize.bind(this);this.loop=this.loop.bind(this);
    this.ro=new ResizeObserver(this.resize);this.ro.observe(container);
    var self=this;this.vis=function(){self.hidden=document.hidden};document.addEventListener('visibilitychange',this.vis);
    this.io=new IntersectionObserver(function(e){self.inView=!!(e[0]&&e[0].isIntersecting)},{threshold:.05});this.io.observe(container);
    this.resize();this.raf=requestAnimationFrame(this.loop);
  }

  Engine.prototype.resize=function(){
    var r=this.c.getBoundingClientRect();this.w=Math.max(120,r.width||200);this.h=Math.max(140,r.height||200);
    this.cv.width=Math.round(this.w*DPR);this.cv.height=Math.round(this.h*DPR);this.ctx.setTransform(DPR,0,0,DPR,0,0);
    this.base=this.h*.9;this.parts=[];this.build();
  };
  Engine.prototype.setStage=function(n){n=clamp(n||1,1,8);if(n===this.targetStage)return;if(this.rm){this.stage=this.targetStage=n;this.build();return}
    this.fromStage=this.stage;this.targetStage=n;this.trans=0;this.transDur=2200+Math.random()*700;};
  Engine.prototype.setMood=function(m){if(!MOOD[m])m='fresh';if(m===this.mood)return;this.prevMood=this.mood;this.mood=m;this.moodBlend=0};
  Engine.prototype.setLevelColor=function(hex){this.levelCol=(typeof hex==='string'&&hex.charAt(0)==='#')?hex:null;};
  Engine.prototype.setXp=function(x,max){
    var prev=this.lastXpSeen, next=x||0;
    this.xp=next;this.maxXp=max||this.maxXp||100;
    if(next>prev+0.0001){
      var jump=Math.min(1,(next-prev)/Math.max(1,this.maxXp*.25));
      this.shimmers.push({t:0,dur:1.6+jump*1.1,strength:.55+jump*.45});
    }
    this.lastXpSeen=next;this.build();
  };
  Engine.prototype.destroy=function(){
    cancelAnimationFrame(this.raf);this.ro&&this.ro.disconnect();this.io&&this.io.disconnect();
    document.removeEventListener('visibilitychange',this.vis);this.cv.remove();
  };

  Engine.prototype.progress=function(){return clamp(this.xp/Math.max(1,this.maxXp),0,1)}
  Engine.prototype.stageFloat=function(){
    var p=this.progress()*.65, s=this.stage+p;
    if(this.trans!==undefined)s=lerp(this.fromStage||this.stage,this.targetStage,clamp(this.trans/this.transDur,0,1));
    return clamp(s,1,8.65);
  };
  Engine.prototype.build=function(){
    var sf=this.stageFloat(), s=Math.floor(sf), p=sf-s;
    this.cv.setAttribute('aria-label','Growth tree, '+(STAGE_NAMES[clamp(Math.round(sf),1,8)-1]||'Tree')+', '+this.mood+' mood');
    this.br=[];this.leaves=[];this.fruit=[];this.bud=null;this.flower=null;this.roots=null;this.dirt=null;
    this.seed=9000+s*111+Math.floor(p*100);
    if(sf<4.7)this.buildYoung(sf);else this.buildTree(sf);
  };

  Engine.prototype.addLeaf=function(x,y,a,len,w,seed){
    this.leaves.push({x:x,y:y,a:a,len:len,w:w,seed:seed||0,shake:0,det:false,vx:0,vy:0,life:1});
  };
  Engine.prototype.addFruit=function(x,y,r,seed){
    this.fruit.push({x:x,y:y,r:r,seed:seed||0,fall:false,vx:0,vy:0,grow:1,hide:0});
  };
  Engine.prototype.branch=function(x1,y1,x2,y2,w,seed){
    this.br.push({x1:x1,y1:y1,x2:x2,y2:y2,w:w,seed:seed});
  };

  Engine.prototype.buildYoung=function(sf){
    var H=this.h, x=this.w*.5, b=this.base, r=rng(this.seed), h;
    this.dirt=[[-18,2],[0,-1],[16,1]].map(function(d){return{x:x+d[0],y:b+d[1],r:1+r()*1.3}});
    /* Young stages are scaled up to fill the stage box — a seedling that
       only occupied ~10% of the frame read as broken empty padding. */
    if(sf<1.8){h=H*(.30+.12*(sf-1));this.branch(x,b,x+r()*4-2,b-h,2.2,1);this.addLeaf(x-4,b-h*.55,-2.75,H*.07,H*.024,1);this.addLeaf(x+4,b-h*.55,-.38,H*.07,H*.024,2);return}
    if(sf<2.8){h=H*(.46+.12*(sf-2));this.branch(x,b,x+2,b-h,2.4,1);
      for(var i=0;i<5;i++){var yy=b-h*(.25+i*.15),side=i%2?-1:1;this.addLeaf(x+side*4,yy,side<0?Math.PI+.45:-.45,H*.07,H*.02,i)}
      return}
    if(sf<3.8){h=H*.62;this.branch(x,b,x,b-h,2.6,1);this.addLeaf(x-9,b-h*.35,Math.PI+.35,H*.078,H*.022,3);this.addLeaf(x+9,b-h*.35,-.35,H*.078,H*.022,4);
      this.addLeaf(x-7,b-h*.58,Math.PI+.55,H*.066,H*.019,5);this.addLeaf(x+7,b-h*.58,-.55,H*.066,H*.019,6);
      this.bud={x:x,y:b-h,r:H*.05};return}
    h=H*.66;this.branch(x,b,x,b-h,2.8,1);this.addLeaf(x-10,b-h*.35,Math.PI+.35,H*.08,H*.023,7);this.addLeaf(x+10,b-h*.35,-.35,H*.08,H*.023,8);
    this.addLeaf(x-8,b-h*.55,Math.PI+.55,H*.07,H*.02,9);this.addLeaf(x+8,b-h*.55,-.55,H*.07,H*.02,10);this.flower={x:x,y:b-h,r:H*.08};
  };

  Engine.prototype.buildTree=function(sf){
    var H=this.h,W=this.w,x=W*.5,b=this.base,st=clamp(sf,5,8),depth=Math.round(lerp(3,6,(st-5)/3)),rr=rng(this.seed);
    var trunk=lerp(H*.22,H*.36,(st-5)/3), baseW=lerp(6,W*.11,(st-5)/3), topW=lerp(3,6.5,(st-5)/3);
    this.branch(x,b,x,b-trunk,baseW,2);this.branch(x-baseW*.35,b,x-3,b-trunk*.38,baseW*.45,5);this.branch(x+baseW*.35,b,x+3,b-trunk*.38,baseW*.45,6);
    var self=this, tipY=b-trunk, scale=lerp(H*.11,H*.19,(st-5)/3), spread=lerp(.72,.78,(st-5)/3);
    function rec(x,y,len,ang,w,d,seed,side){
      if(d<=0||len<5){self.addLeaf(x,y,ang,len*.33,H*.012,seed);return}
      var nx=x+Math.cos(ang)*len, ny=y+Math.sin(ang)*len, sway=(rr()-.5)*.28, a1=ang-(.48+rr()*.18)*side+sway, a2=ang+(.34+rr()*.22)*side-sway*.4;
      self.branch(x,y,nx,ny,w,seed);rec(nx,ny,len*spread*(.9+rr()*.18),a1,w*.69,d-1,seed*3+1,side);
      if(d>1)rec(nx,ny,len*spread*(.82+rr()*.22),a2,w*.62,d-1,seed*3+2,-side);
    }
    rec(x,tipY,scale,-Math.PI/2-.23,topW,depth,11,-1);rec(x,tipY,scale,-Math.PI/2+.18,topW,depth,29,1);
    var count=Math.round(lerp(2,7,(st-5)/3));
    for(var i=0;i<count;i++){var lf=this.leaves[Math.floor(rr()*this.leaves.length)]||{x:x,y:tipY};this.addFruit(lf.x+(rr()-.5)*22,lf.y+(rr()-.5)*16,H*.012,77+i)}
    this.roots=[[x,b,x-W*.32,b+1],[x,b,x+W*.32,b+1],[x,b,x-W*.18,b-7],[x,b,x+W*.18,b-7]];
  };

  Engine.prototype.loop=function(now){
    var dt=Math.min(.04,(now-this.last)/1000||.016);this.last=now;
    if(!this.hidden&&this.inView){this.update(dt,now*.001);this.draw(now*.001)}
    this.raf=requestAnimationFrame(this.loop);
  };
  Engine.prototype.update=function(dt,t){
    if(this.rm)return;
    if(this.trans!==undefined){this.trans+=dt*1000;if(this.trans>=this.transDur){this.stage=this.targetStage;this.trans=undefined}this.build()}
    this.moodBlend=Math.min(1,this.moodBlend+dt/1.5);
    var frame=dt*1000;if(frame>18)this.slow++;else this.slow=Math.max(0,this.slow-2);if(this.slow>30){this.cap=.65;this.slow=0}
    var m=MOOD[this.mood]||MOOD.fresh, amp=m[0], sp=m[1];if(this.mood==='windy')amp*=1+.5*Math.sin(t*.3);
    for(var i=0;i<this.leaves.length;i++){var L=this.leaves[i], n=noise(L.x*.015,L.y*.015,t*sp);L.wind=n*amp;
      L.shake*=Math.pow(.08,dt);
    }
    for(i=this.fruit.length-1;i>=0;i--){var F=this.fruit[i];if(F.grow<1)F.grow=Math.min(1,F.grow+dt*.35)}
    for(i=this.shimmers.length-1;i>=0;i--){this.shimmers[i].t+=dt;if(this.shimmers[i].t>=this.shimmers[i].dur)this.shimmers.splice(i,1)}
    this.updateParticles(dt,t);
  };

  Engine.prototype.puff=function(x,y,col){for(var i=0;i<8;i++)this.puffs.push({x:x,y:y,vx:(Math.random()-.5)*42,vy:-Math.random()*34,r:1+Math.random()*2,a:1,c:col})};
  Engine.prototype.updateParticles=function(dt,t){
    var want=this.mood==='rainy'?Math.floor(46*this.cap):this.mood==='windy'?Math.floor(12*this.cap):this.mood==='radiant'?Math.floor(11*this.cap):this.mood==='breezy'?6:this.mood==='wilt'?0:(this.stageFloat()>=5?6:0);
    while(this.parts.length<want)this.parts.push({life:0,type:''});
    for(var i=0;i<this.parts.length;i++){var p=this.parts[i];if(p.life<=0)this.spawnParticle(p,i,t);p.life-=dt;p.x+=p.vx*dt;p.y+=p.vy*dt;if(p.y>this.base&&p.type==='rain'){this.puff(p.x,this.base,'rgba(160,180,210,.55)');p.life=0}}
    for(i=this.puffs.length-1;i>=0;i--){var q=this.puffs[i];q.a-=dt*1.8;q.x+=q.vx*dt;q.y+=q.vy*dt;q.vy+=80*dt;if(q.a<=0)this.puffs.splice(i,1)}
  };
  Engine.prototype.spawnParticle=function(p,i,t){
    var W=this.w,H=this.h,b=this.base,m=(this.moodBlend<1&&Math.random()>this.moodBlend?this.prevMood:this.mood),r=Math.random;p.life=1+r()*3;p.age=0;
    if(m==='rainy'){p.type='rain';p.x=r()*W+40;p.y=-20-r()*H*.4;p.vx=-95;p.vy=360+r()*90;p.life=2}
    else if(m==='fresh'||m==='radiant'){p.type='fly';p.cx=W*(.28+r()*.48);p.cy=H*(.12+r()*.18);p.x=p.cx;p.y=p.cy;p.vx=p.vy=0;p.life=6+r()*4;p.phase=r()*TWO}
    else{p.type='leaf';p.x=-20-r()*W*.2;p.y=H*(.12+r()*.42);p.vx=(m==='windy'?180:70)+r()*80;p.vy=(m==='windy'?36:22)+r()*42;p.life=(m==='windy'?2.6:5)+r()*2;p.rot=r()*TWO}
  };

  Engine.prototype.draw=function(t){
    var c=this.ctx,W=this.w,H=this.h,b=this.base,sf=this.stageFloat();c.clearRect(0,0,W,H);c.lineCap='round';c.lineJoin='round';
    /* Foliage colour: CEFR-level tint blended with the life-cycle hue so
       progress reads as colour. Falls back to the stage ramp. */
    this.fol=this.levelCol?mix(this.levelCol,STAGE_HEX[clamp(Math.floor(sf),1,8)-1],.32):stageHue(sf);
    this.drawAtmosphere(c,t);
    c.strokeStyle=this.t;c.fillStyle=this.t;c.globalAlpha=.32;c.beginPath();c.moveTo(W*.16,b);c.quadraticCurveTo(W*.5,b-5,W*.84,b);c.stroke();c.globalAlpha=1;
    this.drawWeatherBack(c,t);
    /* Subtle breathing sway — the whole tree drifts within a hair
       of a degree, like it's quietly inhaling. Anchored at the soil
       so the roots stay put. */
    var sway=Math.sin(t*.42)*0.006+Math.sin(t*.17)*0.004;
    c.save();c.translate(W*.5,b);c.rotate(sway);c.translate(-W*.5,-b);
    this.drawRoots(c);
    if(sf>=5)this.drawCanopyGlow(c,sf,t);
    this.drawBranches(c,t);this.drawShimmers(c,t);this.drawYoungExtras(c,sf,t);this.drawLeaves(c,t);this.drawFruit(c,t);
    c.restore();
    this.drawParticles(c,t);this.drawDirt(c);
  };
  Engine.prototype.drawAtmosphere=function(c,t){
    var W=this.w,H=this.h,m=this.mood,a=this.a;
    /* Warm low-sat wash for fresh, cool slate for rainy, neutral
       for the middle moods. Painted as a single soft radial so it
       reads as "light in the room" rather than a sticker. */
    var cx,cy,r1,r2,stop;
    if(m==='radiant'){cx=W*.7;cy=H*.16;r2=Math.max(W,H)*1.05;stop='rgba(255,224,150,0.30)'}
    else if(m==='fresh'){cx=W*.78;cy=H*.18;r2=Math.max(W,H)*.95;stop='rgba(255,214,158,0.18)'}
    else if(m==='wilt'){cx=W*.5;cy=H*.2;r2=Math.max(W,H);stop='rgba(120,128,140,0.10)'}
    else if(m==='rainy'){cx=W*.4;cy=H*.12;r2=Math.max(W,H)*1.05;stop='rgba(120,140,170,0.16)'}
    else if(m==='windy'){cx=W*.2;cy=H*.22;r2=Math.max(W,H);stop='rgba(180,195,220,0.13)'}
    else {cx=W*.7;cy=H*.22;r2=Math.max(W,H);stop='rgba(210,200,240,0.13)'}
    var g=c.createRadialGradient(cx,cy,8,cx,cy,r2);g.addColorStop(0,stop);g.addColorStop(1,'rgba(0,0,0,0)');
    c.fillStyle=g;c.fillRect(0,0,W,H);
  };
  Engine.prototype.drawCanopyGlow=function(c,sf,t){
    /* Soft accent halo behind the canopy so a mature tree feels
       lit-from-within. Stronger as the stage grows. */
    var W=this.w,H=this.h,b=this.base,trunk=lerp(H*.16,H*.32,(clamp(sf,5,8)-5)/3);
    var cx=W*.5,cy=b-trunk-H*.10,r=H*.30+(sf-5)*H*.035;
    var g=c.createRadialGradient(cx,cy,4,cx,cy,r);
    /* Green foliage halo (not white) so a mature tree glows leafy from
       within rather than reading as a bare white bulb on the dark card. */
    var alpha=.07+.035*(sf-5)+.015*Math.sin(t*.6), fol=this.fol||'#2fc55f';
    g.addColorStop(0,rgba(fol,alpha));
    g.addColorStop(.55,rgba(fol,alpha*.42));
    g.addColorStop(1,rgba(fol,0));
    c.fillStyle=g;c.beginPath();c.arc(cx,cy,r,0,TWO);c.fill();
  };
  Engine.prototype.drawShimmers=function(c,t){
    /* Progress shimmer: ascending sparks along the trunk after XP gain. */
    if(!this.shimmers.length)return;
    var W=this.w,H=this.h,b=this.base,sf=this.stageFloat(),trunk=sf<4.7?H*.42:lerp(H*.16,H*.32,(clamp(sf,5,8)-5)/3);
    var tipY=b-trunk,x=W*.5;
    for(var i=0;i<this.shimmers.length;i++){
      var s=this.shimmers[i],p=clamp(s.t/s.dur,0,1),fade=Math.sin(p*Math.PI),y=lerp(b-4,tipY-6,p),wob=Math.sin(p*8+i)*2;
      c.fillStyle=this.a;c.globalAlpha=.55*fade*s.strength;
      c.beginPath();c.arc(x+wob,y,2.2,0,TWO);c.fill();
      c.globalAlpha=.22*fade*s.strength;
      c.beginPath();c.arc(x+wob,y,5,0,TWO);c.fill();
    }
    c.globalAlpha=1;
  };
  Engine.prototype.drawDirt=function(c){if(!this.dirt)return;c.fillStyle=this.t;c.globalAlpha=.34;for(var i=0;i<this.dirt.length;i++){var d=this.dirt[i];c.beginPath();c.arc(d.x,d.y,d.r,0,TWO);c.fill()}c.globalAlpha=1};
  Engine.prototype.drawRoots=function(c){if(!this.roots)return;c.strokeStyle=this.t;c.globalAlpha=.55;for(var i=0;i<this.roots.length;i++){var r=this.roots[i];this.taper(c,r[0],r[1],r[2],r[3],3,1.2)}c.globalAlpha=1};
  Engine.prototype.taper=function(c,x1,y1,x2,y2,w1,w2){var n=7;for(var i=0;i<n;i++){var a=i/n,b=(i+1)/n;c.lineWidth=lerp(w1,w2,a);c.beginPath();c.moveTo(lerp(x1,x2,a),lerp(y1,y2,a));c.lineTo(lerp(x1,x2,b),lerp(y1,y2,b));c.stroke()}};
  Engine.prototype.drawBranches=function(c,t){
    /* Young plants (≤ Bloom) are mostly stem, so tint the stem with the
       stage colour; mature trees keep a neutral woody outline so the
       coloured canopy reads against it. */
    c.strokeStyle=this.stageFloat()<4.7?this.fol:this.bark;for(var i=0;i<this.br.length;i++){var b=this.br[i],n=noise(b.x2*.01,b.y2*.01,t*.22)*.9,amp=(MOOD[this.mood]||MOOD.fresh)[0],dx=n*amp*10*(1-b.w/18);
      this.taper(c,b.x1,b.y1,b.x2+dx,b.y2,b.w,b.w*.35);if(this.stageFloat()>=7&&b.w>4){c.globalAlpha=.26;c.lineWidth=1;c.beginPath();c.moveTo(lerp(b.x1,b.x2,.45)+2,b.y1+(b.y2-b.y1)*.45);c.lineTo(lerp(b.x1,b.x2,.45)-3,b.y1+(b.y2-b.y1)*.45+5);c.stroke();c.globalAlpha=1}}
  };
  Engine.prototype.drawLeaf=function(c,L,t){
    var a=L.a+(L.wind||0)+(L.shake||0),len=L.len,w=L.w,x=L.x,y=L.y;
    c.save();c.translate(x,y);c.rotate(a);
    /* Soft filled body for a sense of volume, then a hairline outline,
       then a single vein. Keeps the minimal grammar but stops the
       leaves from looking like wireframes. */
    c.beginPath();c.moveTo(0,0);c.bezierCurveTo(len*.35,-w,len*.72,-w,len,0);c.bezierCurveTo(len*.72,w,len*.35,w,0,0);
    c.fillStyle=this.fol;c.globalAlpha=.22;c.fill();
    c.strokeStyle=this.fol;c.globalAlpha=.9;c.lineWidth=1.1;c.stroke();
    c.globalAlpha=.4;c.lineWidth=.9;c.beginPath();c.moveTo(0,0);c.lineTo(len*.86,0);c.stroke();
    c.restore();c.globalAlpha=1;
  };
  Engine.prototype.drawLeaves=function(c,t){for(var i=0;i<this.leaves.length;i++)this.drawLeaf(c,this.leaves[i],t)};
  Engine.prototype.drawFruit=function(c,t){c.fillStyle=this.fruitCol;for(var i=0;i<this.fruit.length;i++){var f=this.fruit[i];if(f.hide>0)continue;c.globalAlpha=.9*f.grow;c.beginPath();c.arc(f.x,f.y,f.r*f.grow,0,TWO);c.fill()}c.globalAlpha=1};
  Engine.prototype.drawYoungExtras=function(c,sf,t){
    if(this.bud){var b=this.bud;c.strokeStyle=this.fol;c.globalAlpha=.88;c.beginPath();c.moveTo(b.x,b.y+b.r);c.bezierCurveTo(b.x-b.r,b.y,b.x-b.r*.4,b.y-b.r,b.x,b.y-b.r*1.2);c.bezierCurveTo(b.x+b.r*.4,b.y-b.r,b.x+b.r,b.y,b.x,b.y+b.r);c.stroke();c.globalAlpha=.2;c.fillStyle=this.fol;c.fill();c.globalAlpha=1}
    if(this.flower){var f=this.flower,PET='#ff6fb5',CTR='#ffce4d';c.strokeStyle=PET;c.fillStyle=PET;for(var i=0;i<5;i++){var a=-Math.PI/2+i*TWO/5;c.globalAlpha=.26;c.beginPath();c.ellipse(f.x+Math.cos(a)*f.r*.45,f.y+Math.sin(a)*f.r*.45,f.r*.48,f.r*.23,a,0,TWO);c.fill();c.globalAlpha=.9;c.stroke()}c.globalAlpha=1;c.fillStyle=CTR;c.beginPath();c.arc(f.x,f.y,f.r*.18,0,TWO);c.fill();c.strokeStyle=CTR;c.globalAlpha=.5;for(i=0;i<7;i++){a=i*TWO/7;c.beginPath();c.moveTo(f.x,f.y);c.lineTo(f.x+Math.cos(a)*f.r*.42,f.y+Math.sin(a)*f.r*.42);c.stroke()}c.globalAlpha=1}
    if(sf>=8){c.fillStyle=this.fol;for(i=0;i<5;i++){c.globalAlpha=.35+.28*Math.sin(t*2+i);c.beginPath();c.arc(this.w*(.28+i*.11),this.h*(.1+.035*(i%2)),1.4,0,TWO);c.fill()}c.globalAlpha=1}
  };
  Engine.prototype.drawWeatherBack=function(c,t){
    if(this.mood==='rainy'){c.strokeStyle='rgba(150,170,200,.42)';c.lineWidth=1.2;c.beginPath();c.arc(this.w*.34+Math.sin(t*.45)*5,this.h*.16,18,Math.PI,TWO);c.arc(this.w*.48+Math.sin(t*.45)*5,this.h*.13,25,Math.PI,TWO);c.arc(this.w*.64+Math.sin(t*.45)*5,this.h*.17,18,Math.PI,TWO);c.stroke()}
    if(this.mood==='windy'){c.strokeStyle='rgba(170,185,210,.22)';c.lineWidth=1;for(var i=0;i<4;i++){c.globalAlpha=.25+.2*Math.sin(t*4+i);c.beginPath();c.moveTo(this.w*.15,this.h*(.28+i*.08));c.lineTo(this.w*.82,this.h*(.22+i*.08));c.stroke()}c.globalAlpha=.35;c.beginPath();c.arc(this.w*.18,this.h*.15,12,Math.PI,TWO);c.arc(this.w*.28,this.h*.13,16,Math.PI,TWO);c.arc(this.w*.39,this.h*.16,11,Math.PI,TWO);c.stroke();c.globalAlpha=1}
  };
  Engine.prototype.drawParticles=function(c,t){for(var i=0;i<this.parts.length;i++){var p=this.parts[i];if(p.life<=0)continue;
    if(p.type==='rain'){c.strokeStyle='rgba(170,190,220,.52)';c.lineWidth=1;c.beginPath();c.moveTo(p.x,p.y);c.lineTo(p.x-7,p.y+18);c.stroke()}
    else if(p.type==='fly'){c.fillStyle=this.a;c.globalAlpha=.35+.3*Math.sin(t*2+p.phase);c.beginPath();c.arc(p.cx+Math.sin(t+p.phase)*8,p.cy+Math.sin(t*1.6+p.phase)*5,1.5,0,TWO);c.fill();c.globalAlpha=1}
    else{c.save();c.translate(p.x,p.y);c.rotate(p.rot+t);c.strokeStyle=this.t;c.globalAlpha=.45;c.lineWidth=1;c.beginPath();c.moveTo(0,0);c.bezierCurveTo(3,-3,7,-3,10,0);c.bezierCurveTo(7,3,3,3,0,0);c.stroke();c.globalAlpha=.22;c.beginPath();c.moveTo(0,0);c.lineTo(8,0);c.stroke();c.restore();c.globalAlpha=1}}
    for(i=0;i<this.puffs.length;i++){p=this.puffs[i];c.fillStyle=p.c;c.globalAlpha=p.a*.45;c.beginPath();c.arc(p.x,p.y,p.r,0,TWO);c.fill()}c.globalAlpha=1};

  function stageInfo(xp){
    var cuts=[0,100,300,600,1000,1500,2500,4000,7000],i=0;for(;i<cuts.length-1;i++)if(xp<cuts[i+1])break;
    i=clamp(i,0,7);return{stage:i+1,min:cuts[i],max:cuts[i+1]}
  }
  function stageFromXp(xp){
    return stageInfo(xp).stage
  }
  function moodFromLegacy(){
    try{var h=document.getElementById('growth-hero');if(h&&h.getAttribute('data-mood'))return h.getAttribute('data-mood')}catch(_){}
    return 'fresh';
  }
  function mount(container,opt){return new Engine(container,opt)}
  function replaceLegacyHook(){
    var stageEl=document.querySelector('.growth-stage');if(!stageEl)return null;
    var old=window.renderGrowth, handle=null;
    function levelHex(d){
      try{
        var lv=String((d&&d.level)||(window.LS&&LS.get&&LS.get('ob_level'))||'A1').toUpperCase().replace('+','');
        if(window.LEVEL_COLORS&&window.LEVEL_COLORS[lv])return window.LEVEL_COLORS[lv];
      }catch(_){}
      return null;
    }
    function sync(d){
      d=d||window._d||{};var xp=d.xp||0,info=stageInfo(xp),max=info.max-info.min,stage=info.stage,mood=moodFromLegacy(),localXp=xp-info.min,lc=levelHex(d);
      if(window.RANKS){try{var r=window.RANKS.find(function(a,i){return xp<((window.RANKS[i+1]||{}).min||9999)});if(r){stage=r.stage||stage;max=(r.max||xp+100)-(r.min||0);localXp=xp-(r.min||0)}}catch(_){}}
      if(!handle)handle=mount(stageEl,{stage:stage,mood:mood,xp:localXp,maxXp:max,levelCol:lc});
      else{handle.setStage(stage);handle.setMood(mood);handle.setLevelColor(lc);handle.setXp(localXp,max)}
    }
    ['growth-svg','growth-weather'].forEach(function(id){var e=document.getElementById(id);if(e)e.remove()});
    window.renderGrowth=function(d){if(old)try{old(d)}catch(_){};sync(d)};
    sync(window._d||{});return handle;
  }
  window.GrowthEngine={mount:mount,replaceLegacyHook:replaceLegacyHook};
})();
