// tracks input in 'search' field (id=general)
let input_so_far = "";

// cached song list and cached queries
var queries = [];
var songs = new Map([]);

// track async fetch and processing
var returned = false;

$("#general").keyup( function() {

  input_so_far = document.getElementsByName("general")[0].value;

  if (input_so_far.length < 3) { 
    $("#table tr").remove();
    return 
  };

  if (!queries.includes(input_so_far.toLowerCase() ) ) {
    queries.push(input_so_far.toLowerCase() );
    returned = false;

    const sanitized_input = encodeURIComponent( input_so_far );
    const url = 'https://' + document.domain + ':' + location.port + '/search?name=' + sanitized_input + '&limit=15&offset=0'

    const LoadData = async () => {
      try {
        const res = await fetch(url);
        console.log("Status code 200 or similar: " + res.ok);
        const data = await res.json();
        return data;
      } catch(err) {
        console.error(err)
      }
    };
  
    LoadData().then(newSongsJson => {
      newSongsJson.forEach( (new_song) => {
        let already_have = false;
        songs.forEach( (_v, key) => {
          if (new_song.id == key) { already_have = true; return; };
        })
        if (!already_have) { songs.set(new_song.utube_id, new_song) }
      })
    }).then( () => { returned = true } );  

  };

  function renderTable () {

    if (returned) {
    
      $("#table tr").remove();
    
      var filtered = new Map(
        [...songs]
        .filter(([k, v]) => 
          ( v.title.toLowerCase().includes( input_so_far.toLowerCase() ) ) || 
          ( v.added_by.toLowerCase().includes( input_so_far.toLowerCase() ) ) )
      );

      filtered.forEach( (song) => {
        let added = song.added_by;
        let added_link = '<a href="/library?name=' + added + '" target="_blank" rel="noopener noreferrer">' + added + '</a>';
        let title  = song.title;
        let id = song.utube_id;
        let id_link = '<a href="https://www.youtube.com/watch?v=' + id + '" target="_blank" rel="noopener noreferrer">' + id + '</a>';
        $('#table tbody').append('<tr><td>'+id_link+'</td><td>'+added_link+'</td><td>'+title+'</td></tr>')
      })

    } else {
      setTimeout(renderTable, 30); // try again in 30 milliseconds
    }
  };

  renderTable();

});
