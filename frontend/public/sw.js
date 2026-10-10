self.addEventListener('push',event=>{
 const data=event.data?event.data.json():{};
 event.waitUntil(self.registration.showNotification(data.title||'Проход',{body:data.body||'Новое уведомление',icon:'/icon.svg'}));
});
self.addEventListener('notificationclick',event=>{
 event.notification.close(); event.waitUntil(clients.openWindow('/'));
});
// Service workers cannot collect phone GPS. No simulated background tracking.
