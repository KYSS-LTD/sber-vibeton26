function database(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open("prohod-device", 1);
    request.onupgradeneeded = () => request.result.createObjectStore("keys");
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}
export async function deviceKey(employeeId: string): Promise<CryptoKeyPair> {
  const db = await database();
  const existing = await new Promise<CryptoKeyPair | undefined>(
    (resolve, reject) => {
      const request = db
        .transaction("keys")
        .objectStore("keys")
        .get(employeeId);
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    },
  );
  if (existing) {
    db.close();
    return existing;
  }
  const pair = await crypto.subtle.generateKey(
    { name: "ECDSA", namedCurve: "P-256" },
    false,
    ["sign", "verify"],
  );
  await new Promise<void>((resolve, reject) => {
    const tx = db.transaction("keys", "readwrite");
    tx.objectStore("keys").put(pair, employeeId);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
  db.close();
  return pair;
}
export async function signChallenge(pair: CryptoKeyPair, challenge: string) {
  const signature = new Uint8Array(
    await crypto.subtle.sign(
      { name: "ECDSA", hash: "SHA-256" },
      pair.privateKey,
      new TextEncoder().encode(challenge),
    ),
  );
  return btoa(String.fromCharCode(...signature))
    .replaceAll("+", "-")
    .replaceAll("/", "_")
    .replaceAll("=", "");
}
