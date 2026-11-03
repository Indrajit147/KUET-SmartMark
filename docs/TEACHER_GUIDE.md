SmartMark - teacher guide
=========================

SmartMark reads the marks and roll number from photos of answer-script covers and fills an
Excel workbook for you. You only check the few values it is unsure about.

WHAT YOU NEED
- The SmartMark folder. Open it and double-click SmartMark.exe. No internet and no installation needed.
- Photos of the covers (JPG or PNG), all in one folder.

TAKING THE PHOTOS
- Lay the cover flat, take the photo from straight above, and fit the whole page in the picture.
- Avoid strong shadows or glare over the marks table and the roll-number boxes.
- Marks must be in red pen. Any background is fine.

LIVE: PHONE PHOTOS ARRIVE ON THEIR OWN (best quality)
- Take each photo with the phone's normal camera app (full quality). Let the photos sync to a folder on the
  laptop, for example:
  * OneDrive app on the phone -> Camera upload ON; on the laptop the photos appear in OneDrive\Pictures\Camera Roll.
  * Syncthing (free, works over Wi-Fi without internet): share the phone's Camera folder with a laptop folder.
- In SmartMark: "Live camera" -> "Phone photos (watch a folder)" -> choose that folder -> Start.
  Every NEW photo that arrives is read and added to the live workbook (photos already there are ignored).
- Set the phone camera to save JPEG (not HEIC). Stop watching when the class is done.

LIVE CAMERA (instead of taking photos first)
- Best with a phone camera. On an Android phone install the free app "IP Webcam", open it, scroll down and tap
  "Start server". It shows an address such as http://192.168.0.105:8080. The phone and the laptop must be on
  the same Wi-Fi. In SmartMark: "Live camera" -> choose "Phone camera" -> type that address -> Start.
- Hold each cover flat under the phone, filling most of the picture. When the green frame appears and the cover
  is still, SmartMark takes the picture by itself, reads it and updates the live workbook
  (output\SmartMark_live_....xlsx). Then put the next cover under the camera. Space = take the picture yourself.
- With the IP Webcam app (1080p photos) hold the phone upright and let the cover fill the whole picture.
  SmartMark then reads it, but a third to two thirds of the values go to "Check flagged". Photos from
  the phone's own camera (folder watch above) need far fewer checks. A laptop webcam is usually too small:
  SmartMark says "photo resolution too low" and asks for a retake - it never guesses from a blurry picture.
- "Undo last" removes the last cover; "New batch" starts a new class.

STEP 1 - SCAN PHOTOS
- Click "Browse...", choose the folder with the photos, then click "Scan".
- About 1 second per photo. The cards show how many covers were read cleanly,
  how many items need a look, and which photos must be retaken.

STEP 2 - CHECK FLAGGED
- SmartMark shows each doubtful item: the photo of what was written, what it read, how sure it is, and why.
- If the value is right, press "Keep". If not, type the right value and press Enter.
  For a crossed-out or empty cell press "Blank".
- Roll number: type all 7 digits. Section: type A or B. "Whole page": look at it and press Keep.
- Up/Down arrows move between items; click any item in the list to jump to it.
- Your answers are saved as you go - you can close SmartMark and continue later.

STEP 3 - ALL COVERS (optional)
- Every cover with its status. Click "View" to see the whole page with what was read
  (orange boxes = values that needed checking).

STEP 4 - EXPORT EXCEL
- Click "Export Excel workbook", then "Open workbook".
- One row per student (the Section A and Section B covers are joined by roll number).
- Each section counts the best 3 of its 4 questions; Final = Section A + Section B.
- Totals are formulas: if you change a mark in Excel, the totals update.
- Any cell still yellow was not checked yet - hover over it to see why it was flagged.

IF SOMETHING GOES WRONG
- "Photo not recognised / retake": the whole cover was not visible or was too blurred. Take the photo again.
- "Roll boxes empty": the student did not fill the roll boxes. Type the roll number from the cover.
- "Section B cover missing": that student's other cover was not in the folder. Add the photo and scan again.
- Every workbook is saved as a new file in the "output" folder, so nothing you edited is ever overwritten.
