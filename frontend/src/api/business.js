import {request} from './client.js';
export const loadAll=async()=>Object.fromEntries(await Promise.all(['sources','periods','policies','runs','schedules','deliveries','audits','comparisons'].map(async k=>[k,await request('/'+k)])));
export const saveSource=(data,id)=>request('/sources'+(id?'/'+id:''),id?'PUT':'POST',{data});
export const savePolicy=data=>request('/policies','POST',{data});
export const submitRun=data=>request('/runs','POST',data);
export const approveRun=(id,note)=>request('/runs/'+id+'/approve','POST',{note});
export const createComparison=data=>request('/comparisons','POST',data);
export const reselectComparison=(id,data)=>request('/comparisons/'+id,'PUT',data);
export const loadPairs=(id,offset,limit=10)=>request('/comparisons/'+id+'/pairs?offset='+offset+'&limit='+limit);
