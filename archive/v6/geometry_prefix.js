'use strict';
// The same pure geometry is embedded in the offline HTML and required by the
// independent SVG renderer. No complete future shape is used for revelation.
(function(root,factory){
  const api=factory();
  if(typeof module==='object'&&module.exports)module.exports=api;
  else root.RibbonPrefix=api;
})(typeof globalThis!=='undefined'?globalThis:this,function(){
  function geometry(stroke){
    const points=stroke.points,left=stroke.left,right=stroke.right;
    if(!Array.isArray(points)||points.length<2||!Array.isArray(left)||!Array.isArray(right)||left.length!==points.length||right.length!==points.length)throw new Error('笔触缺少对应的中心线与左右笔缘采样');
    let lengths;
    if(Array.isArray(stroke.lengths)&&stroke.lengths.length===points.length&&stroke.lengths.every((v,i,a)=>Number.isFinite(v)&&(i===0||v>=a[i-1]))&&stroke.lengths[stroke.lengths.length-1]>stroke.lengths[0])lengths=stroke.lengths.map(v=>v-stroke.lengths[0]);
    else{
      lengths=[0];
      for(let i=1;i<points.length;i++)lengths.push(lengths[i-1]+Math.hypot(points[i][0]-points[i-1][0],points[i][1]-points[i-1][1]));
    }
    // A stationary dab still advances its boundary samples in their order.
    if(lengths[lengths.length-1]===0)lengths=points.map((_,i)=>i);
    return{points,left,right,lengths,length:lengths[lengths.length-1]};
  }
  function partialRibbonD(stroke,progress,prepared){
    const p=Math.max(0,Math.min(1,Number(progress)||0));
    if(p<=0)return'';
    if(p>=1)return stroke.d;
    const g=prepared||geometry(stroke),distance=p*g.length;
    let cut=1;
    while(cut<g.lengths.length-1&&g.lengths[cut]<distance)cut++;
    const fraction=(distance-g.lengths[cut-1])/(g.lengths[cut]-g.lengths[cut-1]||1);
    const interpolate=(a,b)=>[a[0]+(b[0]-a[0])*fraction,a[1]+(b[1]-a[1])*fraction];
    const left=g.left.slice(0,cut),right=g.right.slice(0,cut);
    left.push(interpolate(g.left[cut-1],g.left[cut]));
    right.push(interpolate(g.right[cut-1],g.right[cut]));
    return'M'+left.concat(right.reverse()).map(point=>String(point[0])+','+String(point[1])).join(' L')+' Z';
  }
  return{geometry,partialRibbonD};
});
