# Troubleshooting GIF Generation Issues

## Common Issues and Solutions

### 1. GIF Generation Failing or Timing Out

**Symptoms:**
- GIFs stuck in "processing" status
- Error messages about FFmpeg
- Timeouts during generation

**Solutions:**

#### Check FFmpeg Installation
```bash
ffmpeg -version
```

If FFmpeg is not installed:
- **Windows**: Download from https://ffmpeg.org/download.html and add to PATH
- **macOS**: `brew install ffmpeg`
- **Linux**: `sudo apt-get install ffmpeg` or `sudo yum install ffmpeg`

#### Reduce GIF Complexity
- Lower the FPS (try 10-15 instead of 30)
- Reduce duration (shorter clips process faster)
- Use smaller scale values
- Process fewer GIFs at once

#### Check Video File
- Ensure video file is not corrupted
- Try with a smaller test video first
- Verify video codec is supported by FFmpeg

### 2. "FFmpeg error" Messages

**Common causes:**
- Invalid crop coordinates (outside video bounds)
- Invalid start time or duration
- Corrupted video file
- Insufficient disk space

**Solutions:**
- Verify crop coordinates are within video dimensions
- Check start_time + duration doesn't exceed video length
- Ensure sufficient disk space in `backend/gifs/` directory
- Try regenerating with different parameters

### 3. GIFs Are Too Large

**Solutions:**
- Use the scale parameter to reduce dimensions
- Lower FPS (fewer frames = smaller file)
- Reduce duration
- The two-pass palette method should already optimize file size

### 4. Slow Processing

**Normal behavior:**
- GIF generation is CPU-intensive
- Two-pass palette method takes longer but produces better quality
- Processing is sequential to avoid resource exhaustion

**Optimization tips:**
- Use lower FPS (10-15 is usually sufficient)
- Reduce video resolution before upload
- Process fewer GIFs simultaneously
- Consider using a more powerful server for production

### 5. "Video file not found" Errors

**Solutions:**
- Ensure video download/upload completed successfully
- Check `backend/videos/` directory exists and has write permissions
- Verify video file wasn't deleted

### 6. Crop Coordinates Issues

**Validation:**
- Crop coordinates are automatically validated and clamped to video bounds
- Width and height are adjusted to be even numbers (required by codecs)
- Percent-based crops are converted to pixels

**If crops look wrong:**
- Verify video dimensions are correct
- Check crop method settings (grid/line/manual)
- Ensure crop coordinates make sense for your video size

## Debug Mode

To see detailed error messages, check the backend console output. Errors are logged with full FFmpeg stderr output.

## Performance Tips

1. **For faster processing:**
   - Use FPS: 10-15
   - Duration: 2-5 seconds
   - Scale: 400-600 pixels width
   - Process one GIF at a time

2. **For better quality:**
   - Use FPS: 20-30
   - Higher scale values
   - Longer durations (but expect slower processing)

3. **For production:**
   - Consider using Celery for distributed task processing
   - Use Redis for task queue
   - Implement parallel processing with worker pools
   - Add caching for repeated operations

## Getting Help

If issues persist:
1. Check backend console for detailed error messages
2. Verify FFmpeg version: `ffmpeg -version`
3. Test with a simple, short video first
4. Check system resources (CPU, memory, disk space)
5. Review FFmpeg documentation for advanced options

