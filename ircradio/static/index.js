function ws_connect(ws_url, onData) {
  console.log('connecting');
  var ws = new WebSocket(ws_url);
  ws.onopen = function() {
    // nothing
  };

  ws.onmessage = function(e) {
    console.log('Message:', e.data);
    onData(e.data);
  };

  ws.onclose = function(e) {
    console.log('Socket is closed. Reconnect will be attempted in 2 seconds.', e.reason);
    setTimeout(function() {
      ws_connect(ws_url, onData);
    }, 2000);
  };

  ws.onerror = function(err) {
    console.error('Socket encountered error: ', err.message, 'Closing socket');
    ws.close();
  };
}
